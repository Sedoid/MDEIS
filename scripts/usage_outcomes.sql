-- Completed outcomes since a time, as one JSON array. Read-only: SELECT only.
-- Run by `make usage` through psql with  -v cut=<UTC time>  and  default_transaction_read_only=on.
-- A report is complete when it is released; a lesson plan when it is approved (a plan later
-- superseded was approved first). The conversation of a report is not stored here: usage_report.py
-- finds it from the run id. No learner name, no text: ids, a time and the approver's name only.
SELECT coalesce(json_agg(o ORDER BY o.completed_at, o.outcome_id), '[]'::json)
FROM (
    SELECT report_id AS outcome_id,
           'report' AS kind,
           run_id,
           NULL::text AS conversation_id,
           to_char(released_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS completed_at,
           released_by_name AS who
    FROM reports
    WHERE status = 'released' AND released_at >= :'cut'::timestamptz
    UNION ALL
    SELECT plan_id,
           'lesson_plan',
           run_id,
           NULL::text,
           to_char(approved_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
           approver_name
    FROM lesson_plans
    WHERE status IN ('approved', 'superseded') AND approved_at >= :'cut'::timestamptz
) AS o;
