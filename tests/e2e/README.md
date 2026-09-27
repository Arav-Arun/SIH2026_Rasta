# Browser tests

Playwright specs for the web client. Each `run_*.py` resets the local database,
creates test identities, starts the API and the web client, runs its spec and
writes a report to `artifacts/reports/`.

| Runner | Spec |
|---|---|
| `run_auth.py` | Sign-in and route access (`auth-bootstrap.spec.ts`) |
| `run_command_map.py` | Command overview and accessibility map |
| `run_deliveries.py` | Deliveries and fleet |
| `run_planner.py` | Route planner |
| `run_accessibility.py` | axe-core audit in English, Hindi and Assamese |
| `run_offline_pwa.py` | PWA install, offline reload and data packs |
| `run_content_security.py` | Content Security Policy on every screen |
| `run_demo_story.py` | The full demo story after `scripts/local_demo.py up` |
| `run_specs.py` | Any named specs on a fresh stack |

```bash
npm run test:e2e:install
npm run test:e2e:planner
npm run test:e2e              # specs only, against a stack that is already running
```
