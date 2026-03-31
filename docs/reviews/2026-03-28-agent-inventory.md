# Инвентаризация агентов и runtime-сервисов

Дата: 2026-03-28

## Кратко

- В `.claude/agents` найдено 99 файлов агентных определений.
- По content hash это 98 уникальных определений: найден как минимум один точный дубль.
- Семейств агентных определений: 23.
- Помимо ожидаемых `api`, `observer`, `disable worker` и `telegram poller` в runtime-контуре уже присутствует ещё и `enable worker`, но он не отражён в описании структуры проекта в README.

## Сводка по семействам

| Семейство | Файлов |
|---|---:|
| `v3` | 16 |
| `github` | 13 |
| `flow-nexus` | 9 |
| `templates` | 9 |
| `consensus` | 7 |
| `core` | 5 |
| `optimization` | 5 |
| `sublinear` | 5 |
| `sparc` | 4 |
| `analysis` | 3 |
| `swarm` | 3 |
| `architecture` | 2 |
| `data` | 2 |
| `development` | 2 |
| `devops` | 2 |
| `documentation` | 2 |
| `goal` | 2 |
| `specialized` | 2 |
| `testing` | 2 |
| `browser` | 1 |
| `custom` | 1 |
| `payments` | 1 |
| `sona` | 1 |

## Канонический список агентных определений

### analysis (2 уник.)

- `analysis/analyze-code-quality.md` — алиасы: `analysis/code-review/analyze-code-quality.md`
- `analysis/code-analyzer.md`

### architecture (2 уник.)

- `architecture/arch-system-design.md`
- `architecture/system-design/arch-system-design.md`

### browser (1 уник.)

- `browser/browser-agent.yaml`

### consensus (7 уник.)

- `consensus/byzantine-coordinator.md`
- `consensus/crdt-synchronizer.md`
- `consensus/gossip-coordinator.md`
- `consensus/performance-benchmarker.md`
- `consensus/quorum-manager.md`
- `consensus/raft-manager.md`
- `consensus/security-manager.md`

### core (5 уник.)

- `core/coder.md`
- `core/planner.md`
- `core/researcher.md`
- `core/reviewer.md`
- `core/tester.md`

### custom (1 уник.)

- `custom/test-long-runner.md`

### data (2 уник.)

- `data/data-ml-model.md`
- `data/ml/data-ml-model.md`

### development (2 уник.)

- `development/backend/dev-backend-api.md`
- `development/dev-backend-api.md`

### devops (2 уник.)

- `devops/ci-cd/ops-cicd-github.md`
- `devops/ops-cicd-github.md`

### documentation (2 уник.)

- `documentation/api-docs/docs-api-openapi.md`
- `documentation/docs-api-openapi.md`

### flow-nexus (9 уник.)

- `flow-nexus/app-store.md`
- `flow-nexus/authentication.md`
- `flow-nexus/challenges.md`
- `flow-nexus/neural-network.md`
- `flow-nexus/payments.md`
- `flow-nexus/sandbox.md`
- `flow-nexus/swarm.md`
- `flow-nexus/user-tools.md`
- `flow-nexus/workflow.md`

### github (13 уник.)

- `github/code-review-swarm.md`
- `github/github-modes.md`
- `github/issue-tracker.md`
- `github/multi-repo-swarm.md`
- `github/pr-manager.md`
- `github/project-board-sync.md`
- `github/release-manager.md`
- `github/release-swarm.md`
- `github/repo-architect.md`
- `github/swarm-issue.md`
- `github/swarm-pr.md`
- `github/sync-coordinator.md`
- `github/workflow-automation.md`

### goal (2 уник.)

- `goal/agent.md`
- `goal/goal-planner.md`

### optimization (5 уник.)

- `optimization/benchmark-suite.md`
- `optimization/load-balancer.md`
- `optimization/performance-monitor.md`
- `optimization/resource-allocator.md`
- `optimization/topology-optimizer.md`

### payments (1 уник.)

- `payments/agentic-payments.md`

### sona (1 уник.)

- `sona/sona-learning-optimizer.md`

### sparc (4 уник.)

- `sparc/architecture.md`
- `sparc/pseudocode.md`
- `sparc/refinement.md`
- `sparc/specification.md`

### specialized (2 уник.)

- `specialized/mobile/spec-mobile-react-native.md`
- `specialized/spec-mobile-react-native.md`

### sublinear (5 уник.)

- `sublinear/consensus-coordinator.md`
- `sublinear/matrix-optimizer.md`
- `sublinear/pagerank-analyzer.md`
- `sublinear/performance-optimizer.md`
- `sublinear/trading-predictor.md`

### swarm (3 уник.)

- `swarm/adaptive-coordinator.md`
- `swarm/hierarchical-coordinator.md`
- `swarm/mesh-coordinator.md`

### templates (9 уник.)

- `templates/automation-smart-agent.md`
- `templates/base-template-generator.md`
- `templates/coordinator-swarm-init.md`
- `templates/github-pr-manager.md`
- `templates/implementer-sparc-coder.md`
- `templates/memory-coordinator.md`
- `templates/orchestrator-task.md`
- `templates/performance-analyzer.md`
- `templates/sparc-coordinator.md`

### testing (2 уник.)

- `testing/production-validator.md`
- `testing/tdd-london-swarm.md`

### v3 (16 уник.)

- `v3/adr-architect.md`
- `v3/aidefence-guardian.md`
- `v3/claims-authorizer.md`
- `v3/collective-intelligence-coordinator.md`
- `v3/ddd-domain-expert.md`
- `v3/injection-analyst.md`
- `v3/memory-specialist.md`
- `v3/performance-engineer.md`
- `v3/pii-detector.md`
- `v3/reasoningbank-learner.md`
- `v3/security-architect-aidefence.md`
- `v3/security-architect.md`
- `v3/security-auditor.md`
- `v3/sparc-orchestrator.md`
- `v3/swarm-memory-manager.md`
- `v3/v3-integration-architect.md`

## Runtime-сервисы приложения

### Основные сервисы

- `apps/api/main.py` — FastAPI API для dashboard, настроек, Telegram и Vision.
- `apps/observer_worker/main.py` — основной цикл мониторинга, парсинга, FSM и алертов.
- `apps/disable_worker/main.py` — очередь отключения объявлений.
- `apps/telegram_poller/main.py` — long polling и обработка Telegram update/callback.
- `run_enable_worker.py` — отдельный runtime для задач на включение объявлений.

### Точки входа и orchestration

- `run_observer.py` — точка входа observer.
- `run_disable_worker.py` — точка входа disable worker.
- `run_enable_worker.py` — точка входа enable worker.
- `run.sh` — локальный процесс-оркестратор с PID-файлом и логами.
- `Makefile` — операционные команды bootstrap, verify и запуск контуров.

## Замечания к инвентарю

- В `README.md` перечислены только `api`, `observer`, `disable worker` и `telegram poller`, но фактический runtime уже включает и `enable worker`.
- В агентном каталоге есть точный дубль `analysis/analyze-code-quality.md` и `analysis/code-review/analyze-code-quality.md`.
- В нескольких семействах есть похожие path-alias паттерны (`development/backend/...`, `devops/ci-cd/...`, `documentation/api-docs/...`, `specialized/mobile/...`), которые стоит отдельно нормализовать, если каталог будет использоваться как канонический реестр.
