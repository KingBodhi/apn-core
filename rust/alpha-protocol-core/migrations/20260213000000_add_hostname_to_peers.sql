-- Add hostname column to peer_nodes table
-- Created: 2026-02-13
-- Purpose: Allow peers to announce their device names for easier identification

ALTER TABLE peer_nodes ADD COLUMN hostname TEXT;

CREATE INDEX IF NOT EXISTS idx_peer_nodes_hostname ON peer_nodes(hostname);
