-- Migration 110: Add response column to human_reviews for HITL guidance text.

\c rasa_policy;

ALTER TABLE human_reviews ADD COLUMN IF NOT EXISTS response TEXT;
