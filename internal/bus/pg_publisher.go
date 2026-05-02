package bus

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"regexp"

	_ "github.com/lib/pq"
)

var validChannel = regexp.MustCompile(`^[a-zA-Z_][a-zA-Z0-9_]*$`)

const busTableDDL = `
CREATE TABLE IF NOT EXISTS bus_messages (
    id         BIGSERIAL PRIMARY KEY,
    channel    TEXT NOT NULL,
    envelope   JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bus_status ON bus_messages (channel, status);
`

// PGPub publishes durable messages via PostgreSQL INSERT + NOTIFY.
type PGPub struct {
	db *sql.DB
}

// NewPGPub opens a publisher backed by lib/pq.
func NewPGPub(dsn string) (*PGPub, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, fmt.Errorf("pg publisher: open: %w", err)
	}
	if _, err := db.Exec(busTableDDL); err != nil {
		db.Close()
		return nil, fmt.Errorf("pg publisher: ddl: %w", err)
	}
	return &PGPub{db: db}, nil
}

// Publish inserts an envelope and fires NOTIFY within a single transaction.
func (p *PGPub) Publish(ctx context.Context, channel string, msg *Envelope) error {
	if !validChannel.MatchString(channel) {
		return fmt.Errorf("pg publish: invalid channel %q", channel)
	}
	envJSON, err := msg.ToJSON()
	if err != nil {
		return fmt.Errorf("pg publish: marshal: %w", err)
	}

	tx, err := p.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("pg publish: begin: %w", err)
	}
	defer tx.Rollback()

	var raw json.RawMessage
	if err := json.Unmarshal([]byte(envJSON), &raw); err != nil {
		return fmt.Errorf("pg publish: json unmarshal: %w", err)
	}

	if _, err := tx.ExecContext(ctx,
		"INSERT INTO bus_messages (channel, envelope) VALUES ($1, $2)",
		channel, raw,
	); err != nil {
		return fmt.Errorf("pg publish: insert: %w", err)
	}

	// NOTIFY wakes up the subscriber; payload is unused by fetchAndMark.
	if _, err := tx.ExecContext(ctx,
		fmt.Sprintf("NOTIFY %s", safeIdent(channel)),
	); err != nil {
		return fmt.Errorf("pg publish: notify: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("pg publish: commit: %w", err)
	}
	return nil
}

// safeIdent returns the channel name used directly (validated by regex above).
func safeIdent(channel string) string { return channel }

// Close shuts down the DB connection.
func (p *PGPub) Close() error {
	return p.db.Close()
}
