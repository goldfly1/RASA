package memory

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

type SemanticMatch struct {
	ChunkText  string  `json:"chunk_text"`
	NodeID     string  `json:"node_id"`
	Similarity float64 `json:"similarity"`
}

type VectorStore struct {
	db *sql.DB
}

func NewVectorStore(dsn string) (*VectorStore, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, fmt.Errorf("vector store: open: %w", err)
	}
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("vector store: ping: %w", err)
	}
	return &VectorStore{db: db}, nil
}

func (s *VectorStore) Close() error {
	return s.db.Close()
}

func (s *VectorStore) Search(ctx context.Context, embedding []float64, k int) ([]SemanticMatch, error) {
	if k <= 0 {
		k = 5
	}

	vecStr := formatVector(embedding)
	query := `
		SELECT chunk_text, node_id::text,
		       1 - (embedding <=> $1::vector) AS similarity
		FROM embeddings
		ORDER BY embedding <=> $1::vector
		LIMIT $2`

	rows, err := s.db.QueryContext(ctx, query, vecStr, k)
	if err != nil {
		return nil, fmt.Errorf("vector search: %w", err)
	}
	defer rows.Close()

	var matches []SemanticMatch
	for rows.Next() {
		var m SemanticMatch
		if err := rows.Scan(&m.ChunkText, &m.NodeID, &m.Similarity); err != nil {
			continue
		}
		matches = append(matches, m)
	}
	return matches, rows.Err()
}

func formatVector(v []float64) string {
	if len(v) == 0 {
		return "[]"
	}
	parts := make([]string, len(v))
	for i, f := range v {
		parts[i] = fmt.Sprintf("%f", f)
	}
	return "[" + strings.Join(parts, ",") + "]"
}
