# WWC Event Types Registry V1

Machine-readable statuses for cabinet overview and analytics planning.  
Status vocabulary: `implemented` | `staging` | `production` | `gap` | `deprecated`.

| event_type | status | channel | production |
|---|---|---|---|
| visitor_first_seen | implemented | core_journey | false |
| route_opened | implemented | core_journey | false |
| ref_seen | implemented | core_journey | false |
| market_selected | implemented | core_journey | false |
| campaign_attributed | implemented | core_journey | false |
| telegram_link_created | implemented | core_identity | false |
| telegram_link_exchanged | implemented | core_identity | false |
| lead_created | implemented | core_leads | true |
| lead_form_start | production | yandex_metrika | true |
| lead_submit_success | production | yandex_metrika | true |
| ref_visit | production | yandex_metrika | true |
| admin_login_approved | implemented | admin_audit | false |
| admin_sensitive_view | implemented | admin_audit | false |
| website_events_writer | gap | legacy_ddl | false |

Source of truth in code: `app/admin/events_registry.py`.
