# WHIEDA deploy hosts

| Alias | URL / host | Use |
|---|---|---|
| `whieda-n8n` | `185.252.232.93` (SSH `root`) | n8n Docker, `publish:workflow`, restart |
| n8n API | `https://sysarchn8n.duckdns.org` | REST + webhooks |
| Site | `173.249.45.83` | nginx `/var/www/whieda-sysarch` (read-only for backend dev) |

SSH: `ssh whieda-n8n` (password in publish scripts until env migration).
