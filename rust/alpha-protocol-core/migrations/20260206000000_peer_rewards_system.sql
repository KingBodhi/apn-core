-- Peer Rewards System - Track contributions and distribute VIBE rewards
-- Created: 2026-02-06
-- Purpose: Implement complete peer reward distribution for APN network

-- ============================================================================
-- peer_nodes: Track all nodes in the network
-- ============================================================================
CREATE TABLE IF NOT EXISTS peer_nodes (
    id BLOB PRIMARY KEY,
    -- Node identification
    node_id TEXT NOT NULL UNIQUE,  -- apn_xxxxxxxx
    peer_id TEXT,  -- LibP2P peer ID
    wallet_address TEXT NOT NULL UNIQUE,  -- Aptos wallet for rewards

    -- Node capabilities
    capabilities TEXT,  -- JSON array: ["compute", "relay", "storage"]

    -- Resources (latest snapshot)
    cpu_cores INTEGER,
    ram_mb INTEGER,
    storage_gb INTEGER,
    gpu_available BOOLEAN DEFAULT 0,
    gpu_model TEXT,

    -- Network info
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    last_heartbeat_at TEXT,

    -- Status
    is_active BOOLEAN DEFAULT 1,
    is_banned BOOLEAN DEFAULT 0,
    ban_reason TEXT,

    created_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_peer_nodes_node_id ON peer_nodes(node_id);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_wallet ON peer_nodes(wallet_address);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_active ON peer_nodes(is_active, is_banned);
CREATE INDEX IF NOT EXISTS idx_peer_nodes_last_heartbeat ON peer_nodes(last_heartbeat_at);

-- ============================================================================
-- peer_contributions: Track resource contributions over time
-- ============================================================================
CREATE TABLE IF NOT EXISTS peer_contributions (
    id BLOB PRIMARY KEY,
    peer_node_id BLOB NOT NULL REFERENCES peer_nodes(id),

    -- Time period for this contribution
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,

    -- Contribution metrics
    uptime_seconds INTEGER NOT NULL DEFAULT 0,
    cpu_units INTEGER NOT NULL DEFAULT 0,
    gpu_units INTEGER NOT NULL DEFAULT 0,
    bandwidth_bytes INTEGER NOT NULL DEFAULT 0,
    storage_bytes INTEGER NOT NULL DEFAULT 0,
    relay_messages INTEGER NOT NULL DEFAULT 0,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed INTEGER NOT NULL DEFAULT 0,
    heartbeat_count INTEGER NOT NULL DEFAULT 0,

    -- Calculated score
    contribution_score INTEGER NOT NULL DEFAULT 0,

    created_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_peer_contrib_node ON peer_contributions(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_peer_contrib_period ON peer_contributions(period_start, period_end);

-- ============================================================================
-- peer_rewards: Individual reward transactions
-- ============================================================================
CREATE TABLE IF NOT EXISTS peer_rewards (
    id BLOB PRIMARY KEY,
    peer_node_id BLOB NOT NULL REFERENCES peer_nodes(id),
    contribution_id BLOB REFERENCES peer_contributions(id),

    -- Reward details
    reward_type TEXT NOT NULL CHECK(reward_type IN (
        'heartbeat',    -- Basic uptime reward
        'task',         -- Task completion bonus
        'resource',     -- Resource contribution
        'mining',       -- Mining contribution
        'bonus'         -- Special bonus/airdrop
    )),

    -- Amount calculation
    base_amount INTEGER NOT NULL,  -- Base VIBE amount (in smallest units)
    multiplier REAL NOT NULL DEFAULT 1.0,  -- Resource multipliers (GPU, etc)
    final_amount INTEGER NOT NULL,  -- base_amount * multiplier

    -- Distribution status
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending',      -- Calculated, not yet distributed
        'batched',      -- Added to a distribution batch
        'distributed',  -- Sent to blockchain
        'confirmed',    -- Confirmed on blockchain
        'failed'        -- Distribution failed
    )),

    -- Blockchain reference
    batch_id BLOB REFERENCES reward_batches(id),
    aptos_tx_hash TEXT,
    block_height INTEGER,

    -- Error handling
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    -- Metadata
    description TEXT,
    metadata TEXT,  -- JSON for additional data

    created_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    distributed_at TEXT,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_peer_rewards_node ON peer_rewards(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_status ON peer_rewards(status);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_type ON peer_rewards(reward_type);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_batch ON peer_rewards(batch_id);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_created ON peer_rewards(created_at);
CREATE INDEX IF NOT EXISTS idx_peer_rewards_tx_hash ON peer_rewards(aptos_tx_hash);

-- ============================================================================
-- reward_batches: Batch multiple rewards into single blockchain transactions
-- ============================================================================
CREATE TABLE IF NOT EXISTS reward_batches (
    id BLOB PRIMARY KEY,

    -- Batch details
    batch_number INTEGER NOT NULL,  -- Sequential batch number
    total_rewards INTEGER NOT NULL,  -- Number of rewards in batch
    total_amount INTEGER NOT NULL,   -- Total VIBE to distribute

    -- Distribution
    from_wallet TEXT NOT NULL,  -- Rewards wallet address
    aptos_tx_hash TEXT,
    block_height INTEGER,
    gas_used INTEGER,

    -- Status
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending',     -- Created, not yet sent
        'submitted',   -- Sent to blockchain
        'confirmed',   -- Confirmed on chain
        'failed'       -- Failed to distribute
    )),

    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    created_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec')),
    submitted_at TEXT,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_reward_batches_status ON reward_batches(status);
CREATE INDEX IF NOT EXISTS idx_reward_batches_number ON reward_batches(batch_number);
CREATE INDEX IF NOT EXISTS idx_reward_batches_tx_hash ON reward_batches(aptos_tx_hash);

-- ============================================================================
-- peer_wallet_balances: Cache of peer wallet balances
-- ============================================================================
CREATE TABLE IF NOT EXISTS peer_wallet_balances (
    peer_node_id BLOB PRIMARY KEY REFERENCES peer_nodes(id),

    -- Calculated balances (from our database)
    pending_rewards INTEGER NOT NULL DEFAULT 0,    -- Rewards not yet distributed
    distributed_rewards INTEGER NOT NULL DEFAULT 0, -- Sent to blockchain
    confirmed_rewards INTEGER NOT NULL DEFAULT 0,   -- Confirmed on chain

    -- On-chain balance (from Aptos)
    onchain_balance INTEGER,
    onchain_last_checked TEXT,

    -- Stats
    total_earned_lifetime INTEGER NOT NULL DEFAULT 0,
    total_withdrawn INTEGER NOT NULL DEFAULT 0,

    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec'))
);

-- ============================================================================
-- reward_distribution_log: Audit trail for all distributions
-- ============================================================================
CREATE TABLE IF NOT EXISTS reward_distribution_log (
    id BLOB PRIMARY KEY,

    -- What happened
    event_type TEXT NOT NULL CHECK(event_type IN (
        'batch_created',
        'batch_submitted',
        'batch_confirmed',
        'batch_failed',
        'reward_calculated',
        'reward_distributed',
        'balance_updated',
        'error'
    )),

    -- References
    peer_node_id BLOB REFERENCES peer_nodes(id),
    reward_id BLOB REFERENCES peer_rewards(id),
    batch_id BLOB REFERENCES reward_batches(id),

    -- Details
    amount INTEGER,
    description TEXT,
    metadata TEXT,  -- JSON

    created_at TEXT NOT NULL DEFAULT (datetime('now', 'subsec'))
);

CREATE INDEX IF NOT EXISTS idx_reward_log_event ON reward_distribution_log(event_type);
CREATE INDEX IF NOT EXISTS idx_reward_log_peer ON reward_distribution_log(peer_node_id);
CREATE INDEX IF NOT EXISTS idx_reward_log_created ON reward_distribution_log(created_at);

-- ============================================================================
-- Views for easy querying
-- ============================================================================

-- Active peers with their current stats
CREATE VIEW IF NOT EXISTS v_active_peers AS
SELECT
    p.id,
    p.node_id,
    p.wallet_address,
    p.cpu_cores,
    p.ram_mb,
    p.storage_gb,
    p.gpu_available,
    p.gpu_model,
    p.last_heartbeat_at,
    CAST((julianday('now') - julianday(p.last_heartbeat_at)) * 24 * 60 AS INTEGER) as minutes_since_heartbeat,
    b.pending_rewards,
    b.confirmed_rewards,
    b.total_earned_lifetime
FROM peer_nodes p
LEFT JOIN peer_wallet_balances b ON b.peer_node_id = p.id
WHERE p.is_active = 1 AND p.is_banned = 0;

-- Pending rewards summary by peer
CREATE VIEW IF NOT EXISTS v_pending_rewards_summary AS
SELECT
    pr.peer_node_id,
    pn.node_id,
    pn.wallet_address,
    COUNT(*) as pending_count,
    SUM(pr.final_amount) as total_pending_vibe,
    MIN(pr.created_at) as oldest_reward_at
FROM peer_rewards pr
JOIN peer_nodes pn ON pn.id = pr.peer_node_id
WHERE pr.status = 'pending'
GROUP BY pr.peer_node_id, pn.node_id, pn.wallet_address;

-- Recent distribution activity
CREATE VIEW IF NOT EXISTS v_recent_distributions AS
SELECT
    rb.id,
    rb.batch_number,
    rb.total_rewards,
    rb.total_amount,
    rb.status,
    rb.aptos_tx_hash,
    rb.created_at,
    rb.confirmed_at,
    COUNT(pr.id) as rewards_in_batch
FROM reward_batches rb
LEFT JOIN peer_rewards pr ON pr.batch_id = rb.id
GROUP BY rb.id
ORDER BY rb.created_at DESC
LIMIT 50;
