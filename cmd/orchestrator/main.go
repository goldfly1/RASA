package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/google/uuid"
	_ "github.com/lib/pq"

	"github.com/goldf/rasa/internal/bus"
	"github.com/goldf/rasa/internal/db"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: orchestrator <command> [args]")
		fmt.Fprintln(os.Stderr, "  submit  --soul <id> --title <text> [--goal <text>] [--wait] [--timeout <s>]")
		fmt.Fprintln(os.Stderr, "  list    [--status <s>] [--soul <id>] [--limit <n>]")
		fmt.Fprintln(os.Stderr, "  show    <task-id>")
		fmt.Fprintln(os.Stderr, "  cancel  <task-id>")
		fmt.Fprintln(os.Stderr, "  retry   <task-id>")
		os.Exit(1)
	}

	switch os.Args[1] {
	case "submit":
		cmdSubmit()
	case "list":
		cmdList()
	case "show":
		cmdShow()
	case "cancel":
		cmdCancel()
	case "retry":
		cmdRetry()
	default:
		log.Fatalf("unknown command: %s", os.Args[1])
	}
}

func cmdSubmit() {
	fs := flag.NewFlagSet("submit", flag.ExitOnError)
	soulID := fs.String("soul", "", "Soul ID (e.g. coder-v2-dev)")
	title := fs.String("title", "", "Task title")
	goal := fs.String("goal", "", "Task goal / prompt text")
	dsnFlag := fs.String("db", "", "PostgreSQL DSN (default: env-based rasa_orch)")
	wait := fs.Bool("wait", true, "Wait for task completion")
	timeout := fs.Int("timeout", 120, "Wait timeout in seconds")
	fs.Parse(os.Args[2:])

	if *soulID == "" || *title == "" {
		log.Fatal("--soul and --title are required")
	}

	dsn := *dsnFlag
	if dsn == "" {
		dsn = db.DSN("rasa_orch")
	}

	pgDB, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer pgDB.Close()

	taskID := uuid.New().String()
	correlationID := uuid.New().String()

	payload := map[string]string{
		"type": "ad-hoc",
		"goal": *goal,
	}
	payloadJSON, _ := json.Marshal(payload)

	desc := *goal
	if desc == "" {
		desc = *title
	}

	_, err = pgDB.ExecContext(context.Background(),
		`INSERT INTO tasks (id, correlation_id, title, description, payload, status, soul_id, priority)
		 VALUES ($1, $2, $3, $4, $5, 'PENDING', $6, 5)`,
		taskID, correlationID, *title, desc, string(payloadJSON), *soulID,
	)
	if err != nil {
		log.Fatalf("insert task: %v", err)
	}
	log.Printf("created task %s (correlation=%s, soul=%s)", taskID[:8], correlationID[:8], *soulID)

	// Set status to ASSIGNED so the agent dispatcher can pick it up
	_, err = pgDB.ExecContext(context.Background(),
		`UPDATE tasks SET status = 'ASSIGNED', assigned_at = NOW(), assigned_agent_id = $1 WHERE id = $2`,
		*soulID, taskID)
	if err != nil {
		log.Fatalf("assign task: %v", err)
	}

	pgPub, err := bus.NewPGPub(dsn)
	if err != nil {
		log.Fatalf("pg pub: %v", err)
	}
	defer pgPub.Close()

	env, err := bus.NewEnvelope("orchestrator", "pool-controller",
		map[string]string{
			"task_id": taskID,
			"title":   *title,
			"goal":    *goal,
		},
		bus.Metadata{
			SoulID: *soulID,
			TaskID: taskID,
		},
		correlationID,
	)
	if err != nil {
		log.Fatalf("envelope: %v", err)
	}

	if err := pgPub.Publish(context.Background(), "tasks_assigned", env); err != nil {
		log.Fatalf("publish: %v", err)
	}
	log.Printf("published to tasks_assigned")

	if *wait {
		waitForCompletion(dsn, taskID, time.Duration(*timeout)*time.Second)
	} else {
		fmt.Printf(`{"task_id":"%s"}`+"\n", taskID)
	}
}

func waitForCompletion(dsn, taskID string, timeout time.Duration) {
	pgSub, err := bus.NewPGSub(dsn)
	if err != nil {
		log.Fatalf("pg sub: %v", err)
	}
	defer pgSub.Close()

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	done := make(chan struct{})
	var finalStatus string
	var resultJSON []byte

	// Attempt NOTIFY-based wait (may race with listener setup)
	pgSub.Subscribe(ctx, "task_completed", func(env *bus.Envelope) {
		if env.Metadata.TaskID != taskID {
			return
		}
		db2, err := sql.Open("postgres", dsn)
		if err != nil {
			return
		}
		defer db2.Close()
		var status string
		var result []byte
		err = db2.QueryRowContext(ctx,
			"SELECT status::text, result FROM tasks WHERE id = $1", taskID,
		).Scan(&status, &result)
		if err != nil {
			return
		}
		finalStatus = status
		if result != nil {
			resultJSON = result
		}
		log.Printf("task %s -> %s", taskID[:8], status)
		close(done)
	})

	log.Printf("waiting up to %v for task completion...", timeout)

	// Poll as fallback (avoids listener race on Windows)
	poll := time.NewTicker(2 * time.Second)
	defer poll.Stop()

	loop:
	for {
		select {
		case <-done:
			break loop
		case <-poll.C:
			db2, err := sql.Open("postgres", dsn)
			if err == nil {
				var status string
				var result []byte
				err = db2.QueryRowContext(ctx,
					"SELECT status::text, result FROM tasks WHERE id = $1 AND status IN ('COMPLETED','FAILED')", taskID,
				).Scan(&status, &result)
				db2.Close()
				if err == nil {
					finalStatus = status
					resultJSON = result
					log.Printf("task %s -> %s (poll)", taskID[:8], status)
					break loop
				}
			}
		case <-ctx.Done():
			log.Fatal("timed out waiting for task completion")
		}
	}

	if resultJSON != nil {
		var pretty json.RawMessage
		if json.Unmarshal(resultJSON, &pretty) == nil {
			out, _ := json.MarshalIndent(pretty, "", "  ")
			fmt.Println(string(out))
		} else {
			fmt.Println(string(resultJSON))
		}
	}
	fmt.Printf("\ntask %s -> %s\n", taskID[:8], finalStatus)
}

func cmdList() {
	fs := flag.NewFlagSet("list", flag.ExitOnError)
	status := fs.String("status", "", "Filter by status (PENDING, ASSIGNED, RUNNING, COMPLETED, FAILED, etc.)")
	soul := fs.String("soul", "", "Filter by soul_id")
	limit := fs.Int("limit", 20, "Max rows to return")
	dsnFlag := fs.String("db", "", "PostgreSQL DSN (default: env-based rasa_orch)")
	fs.Parse(os.Args[2:])

	dsn := *dsnFlag
	if dsn == "" {
		dsn = db.DSN("rasa_orch")
	}

	pgDB, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer pgDB.Close()

	query := `SELECT id, status::text, soul_id, title, created_at FROM tasks WHERE 1=1`
	var args []any
	argIdx := 1

	if *status != "" {
		query += fmt.Sprintf(" AND status = $%d", argIdx)
		args = append(args, *status)
		argIdx++
	}
	if *soul != "" {
		query += fmt.Sprintf(" AND soul_id = $%d", argIdx)
		args = append(args, *soul)
		argIdx++
	}
	query += fmt.Sprintf(" ORDER BY created_at DESC LIMIT $%d", argIdx)
	args = append(args, *limit)

	rows, err := pgDB.QueryContext(context.Background(), query, args...)
	if err != nil {
		log.Fatalf("query: %v", err)
	}
	defer rows.Close()

	fmt.Printf("%-10s %-14s %-18s %s\n", "ID", "STATUS", "SOUL", "TITLE")
	fmt.Println("---------- -------------- ------------------ --------------------")
	for rows.Next() {
		var id, st, sid, title string
		var createdAt time.Time
		if err := rows.Scan(&id, &st, &sid, &title, &createdAt); err != nil {
			log.Fatalf("scan: %v", err)
		}
		if len(title) > 50 {
			title = title[:47] + "..."
		}
		fmt.Printf("%-10s %-14s %-18s %s\n", id[:8], st, sid, title)
	}
}

func cmdShow() {
	if len(os.Args) < 3 {
		log.Fatal("usage: orchestrator show <task-id>")
	}
	taskID := os.Args[2]

	dsn := db.DSN("rasa_orch")
	pgDB, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer pgDB.Close()

	var id, correlationID, title, description, soulID, agentID, status string
	var priority int
	var retryCount int
	var payload, result []byte
	var createdAt, assignedAt, startedAt, completedAt *time.Time

	err = pgDB.QueryRowContext(context.Background(),
		`SELECT id, correlation_id, title, description, status::text, soul_id,
		        assigned_agent_id, priority, retry_count,
		        payload, result, created_at, assigned_at, started_at, completed_at
		 FROM tasks WHERE id = $1`, taskID,
	).Scan(&id, &correlationID, &title, &description, &status, &soulID,
		&agentID, &priority, &retryCount,
		&payload, &result, &createdAt, &assignedAt, &startedAt, &completedAt)
	if err != nil {
		log.Fatalf("query: %v", err)
	}

	fmt.Printf("ID:            %s\n", id)
	fmt.Printf("Correlation:   %s\n", correlationID[:8])
	fmt.Printf("Title:         %s\n", title)
	fmt.Printf("Description:   %s\n", description)
	fmt.Printf("Status:        %s\n", status)
	fmt.Printf("Soul:          %s\n", soulID)
	if agentID != "" {
		fmt.Printf("Agent:         %s\n", agentID)
	}
	fmt.Printf("Priority:      %d\n", priority)
	fmt.Printf("Retry count:   %d\n", retryCount)
	fmt.Printf("Created:       %s\n", fmtTime(createdAt))
	fmt.Printf("Assigned:      %s\n", fmtTime(assignedAt))
	if startedAt != nil {
		fmt.Printf("Started:       %s\n", fmtTime(startedAt))
	}
	if completedAt != nil {
		fmt.Printf("Completed:     %s\n", fmtTime(completedAt))
	}

	if result != nil {
		var pretty json.RawMessage
		if json.Unmarshal(result, &pretty) == nil {
			out, _ := json.MarshalIndent(pretty, "", "  ")
			fmt.Printf("\nResult:\n%s\n", string(out))
		} else {
			fmt.Printf("\nResult:\n%s\n", string(result))
		}
	}
}

func cmdCancel() {
	if len(os.Args) < 3 {
		log.Fatal("usage: orchestrator cancel <task-id>")
	}
	taskID := os.Args[2]

	dsn := db.DSN("rasa_orch")
	pgDB, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer pgDB.Close()

	// Only cancel tasks that are PENDING or ASSIGNED
	res, err := pgDB.ExecContext(context.Background(),
		`UPDATE tasks SET status = 'CANCELLED', updated_at = NOW()
		 WHERE id = $1 AND status IN ('PENDING', 'ASSIGNED')`, taskID)
	if err != nil {
		log.Fatalf("cancel: %v", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		var st string
		pgDB.QueryRowContext(context.Background(),
			"SELECT status::text FROM tasks WHERE id = $1", taskID).Scan(&st)
		if st == "" {
			log.Fatalf("task %s not found", taskID[:8])
		}
		log.Fatalf("task %s is %s — can only cancel PENDING or ASSIGNED tasks", taskID[:8], st)
	}
	fmt.Printf("task %s → CANCELLED\n", taskID[:8])
}

func cmdRetry() {
	if len(os.Args) < 3 {
		log.Fatal("usage: orchestrator retry <task-id>")
	}
	taskID := os.Args[2]

	dsn := db.DSN("rasa_orch")
	pgDB, err := sql.Open("postgres", dsn)
	if err != nil {
		log.Fatalf("db open: %v", err)
	}
	defer pgDB.Close()

	// Only retry FAILED or CANCELLED tasks
	var currentStatus string
	var retryCount int
	var soulID string
	err = pgDB.QueryRowContext(context.Background(),
		`SELECT status::text, COALESCE(retry_count, 0), soul_id FROM tasks WHERE id = $1`, taskID,
	).Scan(&currentStatus, &retryCount, &soulID)
	if err != nil {
		log.Fatalf("task %s not found", taskID[:8])
	}

	if currentStatus != "FAILED" && currentStatus != "CANCELLED" {
		log.Fatalf("task %s is %s — can only retry FAILED or CANCELLED tasks", taskID[:8], currentStatus)
	}

	// Reset to PENDING, increment retry_count, clear agent assignment
	_, err = pgDB.ExecContext(context.Background(),
		`UPDATE tasks SET status = 'PENDING', retry_count = $2, assigned_agent_id = NULL,
		        updated_at = NOW(), completed_at = NULL
		 WHERE id = $1`, taskID, retryCount+1)
	if err != nil {
		log.Fatalf("retry update: %v", err)
	}

	// Publish to tasks_assigned so pool-controller picks it up
	pgPub, err := bus.NewPGPub(dsn)
	if err != nil {
		log.Printf("WARNING: pg pub connect failed, task reset but not published: %v", err)
		fmt.Printf("task %s → PENDING (retry #%d, not published — no pool-controller reachable)\n", taskID[:8], retryCount+1)
		return
	}
	defer pgPub.Close()

	correlationID := uuid.New().String()
	env, _ := bus.NewEnvelope("orchestrator", "pool-controller",
		map[string]string{"task_id": taskID, "action": "retry"},
		bus.Metadata{SoulID: soulID, TaskID: taskID}, correlationID)
	if err := pgPub.Publish(context.Background(), "tasks_assigned", env); err != nil {
		log.Printf("WARNING: publish failed: %v", err)
	}
	fmt.Printf("task %s → PENDING (retry #%d)\n", taskID[:8], retryCount+1)
}

func fmtTime(t *time.Time) string {
	if t == nil {
		return "-"
	}
	return t.Format("2006-01-02 15:04:05")
}
