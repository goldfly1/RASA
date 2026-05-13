package pool

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/goldf/rasa/internal/bus"
)

// PoolController manages the agent pool: registry, heartbeat monitoring, task routing.
type PoolController struct {
	registry *AgentRegistry
	pgSub    *bus.PGSub
	redisSub *bus.RedisSub
	orchDB   *sql.DB
	poolDB   *sql.DB

	mu    sync.Mutex
	hbSeq map[string]int64

	config *PoolConfig
	ctx    context.Context
	cancel context.CancelFunc
}

func NewPoolController(
	ctx context.Context,
	cfg *PoolConfig,
	pgSub *bus.PGSub,
	redisSub *bus.RedisSub,
	orchDB *sql.DB,
	poolDB *sql.DB,
) *PoolController {
	ctx, cancel := context.WithCancel(ctx)
	return &PoolController{
		registry: NewAgentRegistry(),
		pgSub:    pgSub,
		redisSub: redisSub,
		orchDB:   orchDB,
		poolDB:   poolDB,
		hbSeq:    make(map[string]int64),
		config:   cfg,
		ctx:      ctx,
		cancel:   cancel,
	}
}

func (c *PoolController) Registry() *AgentRegistry {
	return c.registry
}

func (c *PoolController) Start() error {
	if err := c.pgSub.Subscribe(c.ctx, "tasks_assigned", c.HandleTaskAssigned); err != nil {
		return err
	}
	if err := c.pgSub.Subscribe(c.ctx, "task_completed", c.HandleTaskCompleted); err != nil {
		return err
	}
	if err := c.redisSub.Subscribe(c.ctx, "agents.heartbeat.*", c.HandleHeartbeat); err != nil {
		return err
	}
	if err := c.redisSub.Start(c.ctx); err != nil {
		return err
	}
	go c.reapLoop()
	log.Println("pool-controller: subscriptions active")
	return nil
}

func (c *PoolController) HandleTaskCompleted(env *bus.Envelope) {
	var p struct {
		TaskID    string `json:"task_id"`
		NewStatus string `json:"new_status"`
	}
	if err := json.Unmarshal(env.Payload, &p); err != nil || p.TaskID == "" {
		return
	}

	log.Printf("pool-controller: task completed (task=%s status=%s)", p.TaskID, p.NewStatus)

	query := `WITH newly_unblocked AS (
		SELECT td.to_task_id
		FROM task_dependencies td
		WHERE td.from_task_id = $1
		AND NOT EXISTS (
			SELECT 1 FROM task_dependencies td2
			JOIN tasks t2 ON td2.from_task_id = t2.id
			WHERE td2.to_task_id = td.to_task_id
			AND t2.status NOT IN ('COMPLETED')
		)
	)
	UPDATE tasks t
	SET status = 'ASSIGNED', assigned_at = NOW()
	FROM newly_unblocked nu
	WHERE t.id = nu.to_task_id
	AND t.status = 'PENDING'
	RETURNING t.id::text, t.soul_id`

	rows, err := c.orchDB.QueryContext(c.ctx, query, p.TaskID)
	if err != nil {
		log.Printf("pool-controller: dependency resolution query: %v", err)
		return
	}
	defer rows.Close()

	for rows.Next() {
		var taskID, soulID string
		if err := rows.Scan(&taskID, &soulID); err != nil {
			continue
		}
		log.Printf("pool-controller: unblocked dependent task %s -> %s", taskID, soulID)

		env, err := bus.NewEnvelope("pool-controller", "tasks_assigned",
			json.RawMessage(fmt.Sprintf(`{"task_id":"%s","soul_id":"%s"}`, taskID, soulID)),
			bus.Metadata{SoulID: soulID, TaskID: taskID},
			"",
		)
		if err != nil {
			continue
		}
		c.HandleTaskAssigned(env)
	}
}

func (c *PoolController) HandleTaskAssigned(env *bus.Envelope) {
	soulID := env.Metadata.SoulID
	taskID := env.Metadata.TaskID
	if taskID == "" {
		var p struct {
			TaskID string `json:"task_id"`
		}
		if err := json.Unmarshal(env.Payload, &p); err == nil && p.TaskID != "" {
			taskID = p.TaskID
		}
	}

	log.Printf("pool-controller: task assigned (soul=%s task=%s)", soulID, taskID)

	agents := c.registry.FindBySoul(soulID)
	if len(agents) == 0 {
		log.Printf("pool-controller: no agent for soul %s, spawning new agent", soulID)
		go c.spawnAgent(soulID)
		return
	}

	chosen := agents[rand.Intn(len(agents))]
	log.Printf("pool-controller: routing task %s -> agent %s (soul=%s)", taskID, chosen, soulID)

	_, err := c.orchDB.ExecContext(c.ctx,
		`UPDATE tasks SET status = 'ASSIGNED', assigned_agent_id = $1, assigned_at = NOW() WHERE id = $2`,
		chosen, taskID,
	)
	if err != nil {
		log.Printf("pool-controller: task assign update: %v", err)
	}
}

func (c *PoolController) recordBackpressure(soulID, taskID string) {
	active := c.registry.Count()
	idle := c.registry.CountByState("IDLE")
	_, err := c.poolDB.ExecContext(c.ctx,
		"INSERT INTO backpressure_events (reason, agents_busy, agents_idle, queue_depth) VALUES ($1, $2, $3, 0)",
		"no_agent_for_soul:"+soulID, active, idle,
	)
	if err != nil {
		log.Printf("pool-controller: backpressure insert: %v", err)
	}
}

func (c *PoolController) HandleHeartbeat(env *bus.Envelope) {
	agentID := env.Metadata.AgentID
	soulID := env.Metadata.SoulID

	var payload struct {
		CurrentState string `json:"current_state"`
		SoulID       string `json:"soul_id"`
	}
	_ = json.Unmarshal(env.Payload, &payload)

	if payload.SoulID != "" {
		soulID = payload.SoulID
	}
	if payload.CurrentState == "" {
		payload.CurrentState = "IDLE"
	}

	info := c.registry.Upsert(agentID, soulID, payload.CurrentState)

	c.mu.Lock()
	c.hbSeq[agentID]++
	seq := c.hbSeq[agentID]
	c.mu.Unlock()

	hbPayload, _ := json.Marshal(map[string]string{
		"state":   payload.CurrentState,
		"soul_id": soulID,
	})
	c.poolDB.ExecContext(c.ctx,
		"INSERT INTO heartbeats (agent_id, seq_num, payload, received_at) VALUES ($1, $2, $3, NOW())",
		agentID, seq, string(hbPayload),
	)

	dbState := payload.CurrentState
	if dbState == "IDLE" {
		dbState = "REGISTERED"
	}
	c.poolDB.ExecContext(c.ctx,
		`INSERT INTO agents (agent_id, soul_id, hostname, state, last_heartbeat, registered_at)
		 VALUES ($1, $2, 'localhost', $3, NOW(), $4)
		 ON CONFLICT (agent_id) DO UPDATE SET state=$3, soul_id=$2, last_heartbeat=NOW()`,
		agentID, soulID, dbState, info.RegisteredAt,
	)
}

// spawnAgent launches a new Python agent process for the given soul.
func (c *PoolController) spawnAgent(soulID string) {
	log.Printf("pool-controller: spawning agent for soul=%s", soulID)
	cmd := exec.Command(
		"powershell.exe", "-Command",
		fmt.Sprintf(`C:\Users\goldf\rasa\.venv\Scripts\python.exe -m rasa.agent.runtime --soul souls/%s.yaml --mode daemon`, soulID),
	)
	cmd.Env = append(cmd.Env, fmt.Sprintf("RASA_DB_PASSWORD=%s", os.Getenv("RASA_DB_PASSWORD")))
	if err := cmd.Start(); err != nil {
		log.Printf("pool-controller: failed to spawn agent for %s: %v", soulID, err)
		return
	}
	log.Printf("pool-controller: spawned agent pid=%d for soul=%s", cmd.Process.Pid, soulID)
}

func (c *PoolController) reapLoop() {
	ticker := time.NewTicker(time.Duration(c.config.Pool.HeartbeatIntervalSeconds) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-c.ctx.Done():
			return
		case <-ticker.C:
			deadline := time.Now().Add(-c.config.DeadAgentTimeout())
			dead := c.registry.RemoveDead(deadline)
			for _, id := range dead {
				log.Printf("pool-controller: agent %s declared dead (timeout)", id)
				c.poolDB.ExecContext(c.ctx,
					`UPDATE agents SET state='DISCONNECTED', disconnected_at=NOW() WHERE agent_id=$1`,
					id,
				)
				c.mu.Lock()
				delete(c.hbSeq, id)
				c.mu.Unlock()
			}
		}
	}
}

func (c *PoolController) Shutdown() {
	c.cancel()
	log.Println("pool-controller: shut down")
}
