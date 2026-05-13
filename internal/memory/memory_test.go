package memory

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	_ "github.com/lib/pq"
)

// --- DB helpers ---

func openTestDB(t *testing.T) *sql.DB {
	t.Helper()
	pw := os.Getenv("RASA_DB_PASSWORD")
	if pw == "" {
		t.Skip("RASA_DB_PASSWORD not set")
	}
	host := env("RASA_DB_HOST", "localhost")
	port := env("RASA_DB_PORT", "5432")
	user := env("RASA_DB_USER", "postgres")
	dsn := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=rasa_memory sslmode=disable", host, port, user, pw)
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		t.Skip("cannot open memory DB: " + err.Error())
	}
	if err := db.Ping(); err != nil {
		db.Close()
		t.Skip("memory DB not reachable: " + err.Error())
	}
	return db
}

func openTestRedis(t *testing.T) *redis.Client {
	t.Helper()
	client := redis.NewClient(&redis.Options{Addr: "localhost:6379"})
	if err := client.Ping(context.Background()).Err(); err != nil {
		t.Skip("redis not running")
	}
	return client
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func cleanupTestData(t *testing.T, db *sql.DB) {
	t.Helper()
	db.Exec(`DELETE FROM embeddings WHERE node_id LIKE 'test-%'`)
	db.Exec(`DELETE FROM canonical_nodes WHERE id LIKE 'test-%'`)
	db.Exec(`DELETE FROM soul_sheets WHERE soul_id LIKE 'test-%'`)
}

// --- CanonicalStore tests ---

func TestUpsertAndGetNode(t *testing.T) {
	db := openTestDB(t)
	defer db.Close()
	cleanupTestData(t, db)
	s := &CanonicalStore{db: db}

	node := CanonicalNode{
		ID:       "test-n1",
		NodeType: "module",
		Name:     "test-module",
		Path:     "pkg/test",
		Body:     `{"key":"value"}`,
	}

	if err := s.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	got, err := s.GetNode(t.Context(), "test-n1")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got == nil {
		t.Fatal("expected node")
	}
	if got.Name != "test-module" {
		t.Errorf("expected 'test-module', got %q", got.Name)
	}
	if got.Body != `{"key":"value"}` {
		t.Errorf("expected body, got %q", got.Body)
	}
}

func TestGetNodeByPath(t *testing.T) {
	db := openTestDB(t)
	defer db.Close()
	cleanupTestData(t, db)
	s := &CanonicalStore{db: db}

	node := CanonicalNode{
		ID:       "test-n2",
		NodeType: "function",
		Name:     "findMe",
		Path:     "pkg/test/findMe.go",
		Body:     `{}`,
	}
	if err := s.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert: %v", err)
	}

	got, err := s.GetNodeByPath(t.Context(), "function", "pkg/test/findMe.go")
	if err != nil {
		t.Fatalf("get by path: %v", err)
	}
	if got == nil {
		t.Fatal("expected node via path lookup")
	}
	if got.ID != "test-n2" {
		t.Errorf("expected test-n2, got %s", got.ID)
	}
}

func TestTraverse(t *testing.T) {
	db := openTestDB(t)
	defer db.Close()
	cleanupTestData(t, db)
	s := &CanonicalStore{db: db}

	// Create a small graph: A → B → C
	a := CanonicalNode{ID: "test-a", NodeType: "module", Name: "A", Body: "{}", OutgoingEdges: []string{"test-b"}}
	b := CanonicalNode{ID: "test-b", NodeType: "module", Name: "B", Body: "{}", OutgoingEdges: []string{"test-c"}}
	c := CanonicalNode{ID: "test-c", NodeType: "module", Name: "C", Body: "{}", OutgoingEdges: nil}

	for _, n := range []CanonicalNode{a, b, c} {
		if err := s.UpsertNode(t.Context(), n); err != nil {
			t.Fatalf("upsert %s: %v", n.ID, err)
		}
	}

	nodes, err := s.Traverse(t.Context(), "test-a", 3)
	if err != nil {
		t.Fatalf("traverse: %v", err)
	}
	if len(nodes) != 3 {
		t.Errorf("expected 3 nodes in traversal, got %d", len(nodes))
	}

	// Depth 1: only A + B
	nodes2, err := s.Traverse(t.Context(), "test-a", 1)
	if err != nil {
		t.Fatalf("traverse depth 1: %v", err)
	}
	if len(nodes2) != 2 {
		t.Errorf("expected 2 nodes at depth 1, got %d", len(nodes2))
	}
}

func TestUpsertAndGetSoulSheet(t *testing.T) {
	db := openTestDB(t)
	defer db.Close()
	cleanupTestData(t, db)
	s := &CanonicalStore{db: db}

	soulJSON := `{"soul_id":"test-coder","memory":{"short_term_window":10}}`
	r := SoulSheetRec{
		SoulID:     "test-coder",
		Version:    "1.0.0",
		AgentRole:  "CODER",
		Body:       soulJSON,
		SourcePath: "souls/test-coder.yaml",
	}
	if err := s.UpsertSoulSheet(t.Context(), r); err != nil {
		t.Fatalf("upsert soul: %v", err)
	}

	got, err := s.GetSoulSheet(t.Context(), "test-coder")
	if err != nil {
		t.Fatalf("get soul: %v", err)
	}
	if got == nil {
		t.Fatal("expected soul sheet")
	}
	if got.AgentRole != "CODER" {
		t.Errorf("expected CODER, got %s", got.AgentRole)
	}

	// Verify the body is valid JSON with memory config
	var body map[string]any
	if err := json.Unmarshal([]byte(got.Body), &body); err != nil {
		t.Fatalf("soul body not valid JSON: %v", err)
	}
	mem, ok := body["memory"].(map[string]any)
	if !ok {
		t.Fatal("expected memory block in soul body")
	}
	if int(mem["short_term_window"].(float64)) != 10 {
		t.Errorf("expected short_term_window=10, got %v", mem["short_term_window"])
	}
}

func TestUpsertNodeUpdate(t *testing.T) {
	db := openTestDB(t)
	defer db.Close()
	cleanupTestData(t, db)
	s := &CanonicalStore{db: db}

	n := CanonicalNode{ID: "test-up", NodeType: "module", Name: "original", Body: "{}"}
	if err := s.UpsertNode(t.Context(), n); err != nil {
		t.Fatalf("upsert1: %v", err)
	}

	n.Name = "updated"
	n.Body = `{"new":"data"}`
	if err := s.UpsertNode(t.Context(), n); err != nil {
		t.Fatalf("upsert2: %v", err)
	}

	got, _ := s.GetNode(t.Context(), "test-up")
	if got.Name != "updated" {
		t.Errorf("expected 'updated', got %q", got.Name)
	}
}

func TestParsePQArray(t *testing.T) {
	tests := []struct {
		input    string
		expected []string
	}{
		{"{}", nil},
		{"", nil},
		{"{abc}", []string{"abc"}},
		{`{"abc","def"}`, []string{"abc", "def"}},
		{"{abc,def}", []string{"abc", "def"}},
	}
	for _, tc := range tests {
		got := parsePQArray(tc.input)
		if len(got) != len(tc.expected) {
			t.Errorf("parsePQArray(%q): expected %d elements, got %d", tc.input, len(tc.expected), len(got))
			continue
		}
		for i := range got {
			if got[i] != tc.expected[i] {
				t.Errorf("parsePQArray(%q)[%d]: expected %q, got %q", tc.input, i, tc.expected[i], got[i])
			}
		}
	}
}

// --- SessionStore tests ---

func TestSessionStoreAppendAndGet(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	s := &SessionStore{client: client}

	soulID := "test-soul"
	taskID := "test-task-" + t.Name()
	defer s.CloseSession(t.Context(), soulID, taskID)

	if err := s.InitSession(t.Context(), soulID, taskID, "agent-1", 5*time.Minute); err != nil {
		t.Fatalf("init: %v", err)
	}

	if err := s.AppendTurn(t.Context(), soulID, taskID, "user", "hello"); err != nil {
		t.Fatalf("append1: %v", err)
	}
	if err := s.AppendTurn(t.Context(), soulID, taskID, "assistant", "hi there"); err != nil {
		t.Fatalf("append2: %v", err)
	}

	turns, err := s.GetRecentTurns(t.Context(), soulID, taskID, 10)
	if err != nil {
		t.Fatalf("get turns: %v", err)
	}
	if len(turns) != 2 {
		t.Fatalf("expected 2 turns, got %d", len(turns))
	}
	if turns[0].Role != "assistant" {
		t.Errorf("expected most recent turn first, got role=%s", turns[0].Role)
	}
	if turns[1].Role != "user" {
		t.Errorf("expected second turn user, got role=%s", turns[1].Role)
	}
}

func TestSessionStoreWindow(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	s := &SessionStore{client: client}

	soulID := "test-soul-w"
	taskID := "test-task-" + t.Name()
	defer s.CloseSession(t.Context(), soulID, taskID)

	s.InitSession(t.Context(), soulID, taskID, "a", 5*time.Minute)
	for i := range 5 {
		s.AppendTurn(t.Context(), soulID, taskID, "user", fmt.Sprintf("msg %d", i))
	}

	turns, _ := s.GetRecentTurns(t.Context(), soulID, taskID, 2)
	if len(turns) != 2 {
		t.Errorf("expected window of 2, got %d", len(turns))
	}
}

func TestSessionStoreEmptySession(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	s := &SessionStore{client: client}

	turns, err := s.GetRecentTurns(t.Context(), "nonexistent", "nonexistent-task", 10)
	if err != nil {
		t.Fatalf("get recent: %v", err)
	}
	if len(turns) != 0 {
		t.Errorf("expected empty turns for nonexistent session, got %d", len(turns))
	}
}

func TestSessionStoreMeta(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	s := &SessionStore{client: client}

	soulID := "test-soul-m"
	taskID := "test-task-" + t.Name()
	defer s.CloseSession(t.Context(), soulID, taskID)

	s.InitSession(t.Context(), soulID, taskID, "agent-42", 5*time.Minute)

	meta, err := s.GetSessionMeta(t.Context(), soulID, taskID)
	if err != nil {
		t.Fatalf("get meta: %v", err)
	}
	if meta == nil {
		t.Fatal("expected meta")
	}
	if meta.AgentID != "agent-42" {
		t.Errorf("expected agent-42, got %s", meta.AgentID)
	}
}

func TestSessionStoreClose(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	s := &SessionStore{client: client}

	soulID := "test-soul-c"
	taskID := "test-task-" + t.Name()
	s.InitSession(t.Context(), soulID, taskID, "a", 5*time.Minute)
	s.AppendTurn(t.Context(), soulID, taskID, "user", "msg")

	if err := s.CloseSession(t.Context(), soulID, taskID); err != nil {
		t.Fatalf("close: %v", err)
	}

	meta, _ := s.GetSessionMeta(t.Context(), soulID, taskID)
	if meta != nil {
		t.Error("expected nil meta after close")
	}

	turns, _ := s.GetRecentTurns(t.Context(), soulID, taskID, 10)
	if len(turns) != 0 {
		t.Errorf("expected empty after close, got %d", len(turns))
	}
}

// --- ContextAssembler tests ---

func TestContextAssembler(t *testing.T) {
	client := openTestRedis(t)
	defer client.Close()
	db := openTestDB(t)
	defer db.Close()

	store := &SessionStore{client: client}
	canonical := &CanonicalStore{db: db}
	assembler := NewContextAssembler(store, canonical, nil, nil)

	soulID := "test-soul-asm"
	taskID := "test-task-" + t.Name()
	defer store.CloseSession(t.Context(), soulID, taskID)

	store.InitSession(t.Context(), soulID, taskID, "a1", 5*time.Minute)
	store.AppendTurn(t.Context(), soulID, taskID, "user", "context test")

	payload, err := assembler.Assemble(t.Context(), AssembleRequest{
		SoulID:    soulID,
		TaskID:    taskID,
		AgentID:   "a1",
		Variables: []string{"short_term_summary", "graph_excerpt", "semantic_matches", "archive_refs"},
		Resolution: map[string]string{},
	})
	if err != nil {
		t.Fatalf("assemble: %v", err)
	}

	if payload.Hash == "" {
		t.Error("expected non-empty hash")
	}

	// short_term_summary should be populated
	sts, ok := payload.Variables["short_term_summary"]
	if !ok {
		t.Error("expected short_term_summary in variables")
	}
	turns, ok := sts.([]interface{})
	if !ok {
		t.Fatalf("short_term_summary is not a slice: %T", sts)
	}
	if len(turns) != 1 {
		t.Errorf("expected 1 turn, got %d", len(turns))
	}

	// graph_excerpt should be empty (no resolution provided)
	_ = payload.Variables["graph_excerpt"]

	// semantic_matches should be empty (deferred)
	_ = payload.Variables["semantic_matches"]

	// archive_refs should be empty (deferred)
	_ = payload.Variables["archive_refs"]
}
