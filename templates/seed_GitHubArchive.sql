-- Database: github_archive
-- Ensure the network and JSON extensions are ready
INSTALL httpfs;
LOAD httpfs;
INSTALL json;
LOAD json;

-- ==========================================
-- 1. VIEW: GITHUB EVENTS (Jan 1, 2024 - 12:00 UTC)
-- ==========================================
CREATE OR REPLACE TABLE github_events AS
SELECT 
    id,
    type AS event_type,
    actor.login AS user_login,
    repo.name AS repo_name,
    created_at::TIMESTAMP AS created_at,
    payload
FROM read_json_auto('https://data.gharchive.org/2024-01-01-12.json.gz');


-- === SNIPPETS ===

-- === SNIPPET START ===
-- Name: Most Active GitHub Repositories
-- Description: Ranks repositories by total events recorded during this hour.
USE github_archive;

SELECT 
    repo_name,
    COUNT(*) AS total_events,
    COUNT(DISTINCT user_login) AS unique_contributors,
    COUNT(CASE WHEN event_type = 'PushEvent' THEN 1 END) AS total_pushes,
    COUNT(CASE WHEN event_type = 'WatchEvent' THEN 1 END) AS total_stars
FROM github_events
GROUP BY ALL
ORDER BY total_events DESC
LIMIT 25;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Event Type Distribution
-- Description: Breaks down the frequency of event types (Push, Watch, PullRequest, etc.) recorded during this period.
USE github_archive;

SELECT 
    event_type,
    COUNT(*) AS event_count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM github_events), 2) AS percentage
FROM github_events
GROUP BY ALL
ORDER BY event_count DESC;
-- === SNIPPET END ===

-- === SNIPPET START ===
-- Name: Pull Request Activity Analysis
-- Description: Extract specific payload fields for PullRequestEvents (action, title, state).
USE github_archive;

SELECT 
    repo_name,
    user_login,
    payload->'action' AS pr_action,
    payload->'pull_request'->>'title' AS pr_title,
    payload->'pull_request'->>'state' AS pr_state,
    payload->'pull_request'->'commits'::INT AS pr_commits
FROM github_events
WHERE event_type = 'PullRequestEvent'
LIMIT 20;
-- === SNIPPET END ===
