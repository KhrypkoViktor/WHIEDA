SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname = 'advisor_review_queue_status_check';
