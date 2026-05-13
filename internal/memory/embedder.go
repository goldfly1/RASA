package memory

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

type Embedder interface {
	Embed(ctx context.Context, text string) ([]float64, error)
}

type OpenAIEmbedder struct {
	apiKey  string
	baseURL string
	model   string
	client  *http.Client
}

func NewOpenAIEmbedder() *OpenAIEmbedder {
	apiKey := os.Getenv("OPENAI_API_KEY")
	baseURL := os.Getenv("OPENAI_BASE_URL")
	if baseURL == "" {
		baseURL = "https://api.openai.com/v1"
	}
	return &OpenAIEmbedder{
		apiKey:  apiKey,
		baseURL: baseURL,
		model:   "text-embedding-3-small",
		client:  &http.Client{Timeout: 30 * time.Second},
	}
}

func NewOllamaEmbedder(baseURL, model string) *OpenAIEmbedder {
	if baseURL == "" {
		baseURL = "http://127.0.0.1:11434/v1"
	}
	if model == "" {
		model = "nomic-embed-text"
	}
	return &OpenAIEmbedder{
		apiKey:  "ollama",
		baseURL: baseURL,
		model:   model,
		client:  &http.Client{Timeout: 60 * time.Second},
	}
}

func (e *OpenAIEmbedder) Embed(ctx context.Context, text string) ([]float64, error) {
	body := map[string]any{
		"model": e.model,
		"input": text,
	}
	bodyJSON, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("embed: marshal: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST",
		e.baseURL+"/embeddings", bytes.NewReader(bodyJSON))
	if err != nil {
		return nil, fmt.Errorf("embed: request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if e.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+e.apiKey)
	}

	resp, err := e.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("embed: do: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("embed: read: %w", err)
	}

	var result struct {
		Data []struct {
			Embedding []float64 `json:"embedding"`
		} `json:"data"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("embed: unmarshal: %w", err)
	}

	if len(result.Data) == 0 {
		return nil, fmt.Errorf("embed: no embedding returned")
	}
	return result.Data[0].Embedding, nil
}
