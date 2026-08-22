SELECT 'conversations' as label, COUNT(*) as cnt FROM ai_conversations
UNION ALL
SELECT 'events', COUNT(*) FROM ai_events
UNION ALL
SELECT 'drafts', COUNT(*) FROM ai_listing_drafts
UNION ALL
SELECT 'account_states', COUNT(*) FROM infradealer_account_states
UNION ALL
SELECT 'chats', COUNT(*) FROM chats
UNION ALL
SELECT 'outbox', COUNT(*) FROM infradealer_outbox
UNION ALL
SELECT 'requests', COUNT(*) FROM infradealer_requests;
