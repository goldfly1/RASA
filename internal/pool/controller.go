package pool

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
	"math/rand"
	"sync"
	"time"

	"github.com/goldf/rasa/internal/bus"
)

// PoolController manages the agent pool: registry, heartbeat monitoring, task routing.
type PoolController struct {
	registry *AgentRegistry
	pgSub    *bus.PGSub
	redisSub *bus.RedisSub
	orchDB   *sql.DB // rasa_orch for task updates
	poolDB   *sql.DB // rasa_pool for heartbeat/backpressure/agent tables

	mu    sync.Mutex
	hbSeq map[string]int64 // agent_id → last heartbeat seq_num

	config *PoolConfig
	ctx    context.Context
	cancel context.CancelFunc
}

// NewPoolController wires the pool controller to its dependencies.
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

// Registry exposes the agent registry for health checks.
func (c *PoolController) Registry() *AgentRegistry {
	return c.registry
}

// Start activates subscriptions and begins the reap loop.
func (c *PoolController) Start() error {
	if err := c.pgSub.Subscribe(c.ctx, "tasks_assigned", c.HandleTaskAssigned); err != nil {
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

// HandleTaskAssigned receives a task_assigned envelope and routes to an agent.
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
		log.Printf("pool-controller: no agent for soul %s, keeping task %s PENDING", soulID, taskID)
		c.recordBackpressure(soulID, taskID)
		return
	}

	// Pick random agent (simple pilot routing)
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

// recordBackpressure inserts a backpressure event when no agent is available.
func (c *PoolController) recordBackpressure(soulID, taskID string) {
	active := c.registry.Count()
	idle := c.registry.CountByState("IDLE")
	_, err := c.poolDB.ExecContext(c.ctx,
		`INSERT INTO backpressure_events (reason, agents_busy, agents_idle, queue_depth)
		 VALUES ($1, $2, $3, 0)`,
		"no_agent_for_soul:"+soulID, active, idle,
	)
	if err != nil {
		log.Printf("pool-controller: backpressure insert: %v", err)
	}
}

// HandleHeartbeat receives an agent heartbeat and updates the registry.
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

	// Bump heartbeat sequence number
	c.mu.Lock()
	c.hbSeq[agentID]++
	seq := c.hbSeq[agentID]
	c.mu.Unlock()

	// Durable heartbeat insert (best-effort)
	hbPayload, _ := json.Marshal(map[string]string{
		"state":   payload.CurrentState,
		"soul_id": soulID,
	})
	c.poolDB.ExecContext(c.ctx,
		`INSERT INTO heartbeats (agent_id, seq_num, payload, received_at)
		 VALUES ($1, $2, $3, NOW())`,
		agentID, seq, string(hbPayload),
	)

	// Upsert into durable agent registry (new agent or state change)
	dbState := payload.CurrentState
	if dbState == "IDLE" {
		dbState = "REGISTERED"
	}
	c.poolDB.ExecContext(c.ctx,
		`INSERT INTO agents (agent_id, soul_id, hostname, state, last_heartbeat, registered_at)
		 VALUES ($1, $2, 'localhost', $3, NOW(), $4)
		 ON CONFLICT (agent_id) DO UPDATE
		 SET state=$3, soul_id=$2, last_heartbeat=NOW()`,
		agentID, soulID, dbState, info.RegisteredAt,
	)
}

// reapLoop periodically removes dead agents.
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

// Shutdown gracefully stops the controller.
func (c *PoolController) Shutdown() {
	c.cancel()
	log.Println("pool-controller: shut down")
}
