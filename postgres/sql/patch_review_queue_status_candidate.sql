alter table advisor_review_queue
  drop constraint if exists advisor_review_queue_status_check;

alter table advisor_review_queue
  add constraint advisor_review_queue_status_check
  check (status in (
    'candidate',
    'pending',
    'triage',
    'in_work',
    'applied',
    'verified',
    'closed',
    'duplicate',
    'rejected'
  ));
