select id || '|' || "activeVersionId" || '|' || "versionId" || '|' || "versionCounter"::text
from workflow_entity
where id = 'advisor-whieda-phase1';
