-- Office update-vs-new + feet/trolley extraction triage (2026-11-02).
-- 176: '22 चक्का' correctly means 22 tyres (not axles) — reject bad domain suggestion.
-- 179/180: user '25 model' / '34 feet trolley' now land in year + body_type/description.
-- 181: office desk posts go live by design; do not hold for prompt-only 'pending'.

UPDATE ai_suggestions
SET status = 'REJECTED',
    reviewed_by = 'agent:office-update-new-1102',
    reviewed_at = NOW()
WHERE status = 'PENDING'
  AND id IN (176, 181);

UPDATE ai_suggestions
SET status = 'NOTED',
    reviewed_by = 'agent:office-update-new-1102',
    reviewed_at = NOW()
WHERE status = 'PENDING'
  AND id IN (179, 180);

SELECT status, kind, count(*) FROM ai_suggestions GROUP BY 1, 2 ORDER BY 1, 2;
SELECT id, status, title FROM ai_suggestions WHERE id IN (176, 179, 180, 181) ORDER BY id;
