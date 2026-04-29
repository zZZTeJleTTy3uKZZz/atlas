"""Запуск 10 блоков deep research в NotebookLM для NP-005 v2.

Блокнот: 9f109c5e-312f-4058-9c98-aee59853c58e
Режим: --mode deep --no-wait (параллельный запуск через ThreadPoolExecutor)
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict

NOTEBOOK_ID = "9f109c5e-312f-4058-9c98-aee59853c58e"

PROMPTS: Dict[str, str] = {
    "A_IT_PM_fundamentals": (
        "Comprehensive 2025-2026 analysis of IT project management fundamentals. Cover: "
        "(1) Major frameworks: PRINCE2, PMBOK 7th edition, PMI Disciplined Agile, SAFe 6.0, "
        "Scrum, Kanban, Scrumban, XP, Shape Up (Basecamp), Lean Startup, ICE/RICE prioritization. "
        "For each: core concepts, fit context, failure modes, 2026 adaptations. "
        "(2) PM roles: Project Manager, Product Manager, Product Owner, Scrum Master, Program Manager, "
        "Delivery Manager, Engineering Manager. Responsibilities, decision rights, RACI boundaries. "
        "(3) PM in contexts: agency/consulting (multi-client capacity), product company, solo founder "
        "(1-5 people), AI-first development. What works, anti-patterns. "
        "(4) Core PM artifacts: Charter, PRD, MRD, tech spec, ADR, backlog, roadmap, burndown, velocity, "
        "risk register, RAID, stakeholder matrix. When to create what, how often to update. "
        "(5) Rituals: daily standup, weekly sync, sprint planning, sprint review, retro, refinement, "
        "PI planning, quarterly review. Value vs theater. AI-augmented team adaptations. "
        "(6) Stakeholder management. "
        "Cite specific books (PMBOK, Scrum Guide 2020, Shape Up), research (Gartner, Forrester, McKinsey), "
        "engineering blogs (First Round, Stripe Press, Linear blog, Basecamp/37signals). "
        "Give concrete examples with named teams/companies."
    ),
    "B_Scrum_detailed": (
        "Deep practical guide to Scrum in 2026 for small teams (1-5 people) and AI-augmented teams "
        "where agents execute most of the work. "
        "(1) Scrum Guide 2020 (Sutherland/Schwaber) core: 3 roles (Product Owner, Scrum Master, Developers), "
        "5 events (Sprint, Planning, Daily, Review, Retro), 3 artifacts (Product Backlog, Sprint Backlog, "
        "Increment). 2020 changes vs 2017. Real interpretations vs theoretical. "
        "(2) Sprint mechanics: length decision (1/2/3/4 weeks), goal formulation (SMART outcome-focused), "
        "capacity planning with focus factor, story points vs T-shirt vs ideal hours, "
        "Definition of Ready vs Definition of Done, commitment vs forecast shift in 2020. "
        "(3) Story writing: User story template, INVEST criteria, Given/When/Then BDD, "
        "epic/feature/story/task hierarchy. "
        "(4) Ceremonies deep-dive: Daily Standup (3 questions vs Walk-the-Wall vs silent async), "
        "Sprint Planning Part 1 + Part 2 time-boxing, Sprint Review demo format, "
        "Retrospective formats (Start/Stop/Continue, 4L, Sailboat, Mad/Sad/Glad, 5 Whys), "
        "Backlog Refinement cadence. "
        "(5) Metrics: velocity, burndown, burnup, cycle time, lead time, WIP limits, escaped defects, "
        "commit-to-complete ratio. Which matter. "
        "(6) Scrum for solo + AI teams: who is PO/SM when one person, self-retrospective techniques, "
        "AI agents as developers (tracking their velocity), ceremony adaptations. "
        "(7) Scrum vs Kanban vs Scrumban for hybrid services + product development. "
        "Cite: Scrum Guide 2020, Sutherland 'Scrum: The Art of Doing Twice the Work', Linear cycle blog, "
        "Basecamp Shape Up, Mike Cohn, Henrik Kniberg 'Scrum and XP from the Trenches', Atlassian Agile Coach."
    ),
    "C_Multi_agent_frameworks": (
        "Comprehensive 2025-2026 comparison of multi-agent orchestration frameworks: "
        "CrewAI, AutoGen (Microsoft Research), LangGraph (LangChain team), LlamaIndex agents, "
        "OpenAI Swarm / new multi-agent SDK, Anthropic SDK subagents, Claude Code subagents, "
        "Codex / OpenHands, Agent Zero, SuperAGI, MetaGPT, ChatDev, AgentVerse, MiniAutoGen, "
        "Pydantic AI, OpenClaw, paperclip, plus any newer 2026 frameworks. "
        "For each evaluate: (1) core architecture (supervisor-worker, democratic, hierarchical, graph-based), "
        "(2) agent communication patterns (shared memory, message passing, API, file-based), "
        "(3) state management (stateless, persistent, checkpointed), "
        "(4) deployment model (SaaS, self-hosted, hybrid), "
        "(5) observability (tracing, logs, cost tracking), "
        "(6) role specialization support (custom personas, tools per role), "
        "(7) integration with external systems (APIs, databases, file systems), "
        "(8) cost model (token costs, hosting, licenses), "
        "(9) community size, maintenance cadence, production deployments, "
        "(10) key strengths and weaknesses. "
        "Decision framework for: solo founder + 5-7 AI-agents (CEO, PM, Marketing, Knowledge, QA, "
        "Developer, Designer); database-backed state (custom PM system); git integration; "
        "Python ecosystem preferred; works with Claude Opus/Sonnet + GPT-5 + Gemini; budget <500 USD/month. "
        "Which framework(s) fit best? Worst? Migration paths between them? "
        "Cite specific production deployments, benchmarks, engineering blogs, github stars, recent releases."
    ),
    "D_Inter_agent_protocols": (
        "Inter-agent communication protocols comparison 2025-2026: "
        "(1) MCP (Model Context Protocol) by Anthropic: architecture, transport (stdio, SSE, HTTP), schema. "
        "Supported clients (Claude Desktop, Claude Code, Cursor, Zed, Cline). Server ecosystem. "
        "Limitations: single-request/response, not full agent-to-agent. Use for multi-agent coordination. "
        "(2) A2A (Agent-to-Agent) emerging standards: Google A2A, OpenAI proposals, academia papers. "
        "Spec, adoption, compatibility with MCP. "
        "(3) ACP (Agent Communication Protocol): history from FIPA-ACL, KQML. "
        "Modern reinterpretations 2024-2026. Practical tools implementing it. "
        "(4) OpenAPI + JSON-RPC approach: treating each agent as a REST/RPC service, "
        "schema registry, versioning, when right choice vs MCP. "
        "(5) Event-driven architectures: Kafka, NATS, Redis streams as message bus for agents, "
        "event sourcing for agent state. "
        "(6) Shared database as coordination: PostgreSQL row-level locking, event tables (action_log), "
        "when better than explicit message passing. "
        "(7) Decision matrix for: 5-7 AI agents + 1 human + future human contractors, "
        "SQLite to PostgreSQL migration path, Python-first with possibility of TypeScript agents, "
        "local-first (Windows 11 laptop) with optional cloud deploy, "
        "observability and debuggability requirements. "
        "Cite: Anthropic MCP documentation (modelcontextprotocol.io), AgentOps blog, "
        "academic papers on agent communication protocols, real deployments."
    ),
    "E_Docs_hierarchy": (
        "IT project documentation hierarchy best practices 2025-2026 for small teams and "
        "AI-augmented development: "
        "(1) Doc types and when to create: Charter / One-pager, MRD (Market Requirements), "
        "PRD (Product Requirements), Design Doc / RFC, Tech Spec, ADR (Architecture Decision Record), "
        "API spec (OpenAPI / GraphQL), User Story / Ticket, Postmortem / Retro, Runbook / SOP. "
        "(2) PRD templates from leading companies: Stripe Press / Patio11, Shreyas Doshi, "
        "Lenny's Newsletter, Figma template, Linear PRD philosophy, Shape Up pitch format (Basecamp), "
        "Amazon 6-pager, Working backwards (Amazon). "
        "(3) ADR practices (Michael Nygard, Gregor Hohpe): Context/Decision/Consequences template, "
        "when to write vs skip, /docs/adr/ storage, numbering, real examples from open-source. "
        "(4) Tech spec vs PRD boundaries: what goes where, anti-pattern conflating them. "
        "(5) Documentation for AI-driven development: AGENTS.md / CLAUDE.md standards, "
        "specs optimized for agent consumption (structured, machine-readable), "
        "Spec-driven development (Kiro, BMAD, 'Point and Call'). "
        "(6) Keeping docs alive: doc-as-code, lint rules for docs, auto-generation from code "
        "(OpenAPI, Pydantic, JSONSchema), review cadence, owner per doc, deprecation protocol. "
        "(7) For solo + AI-team: minimum viable docs per project type (utility / product / client), "
        "when PRD is overkill (personal-utility experiments), automating doc maintenance via AI agents. "
        "Cite: ThoughtWorks TechRadar, Martin Fowler, Atlassian, Shreyas Doshi, Lenny's Newsletter, "
        "Google Engineering Practices."
    ),
    "F_DB_schemas": (
        "Database schema patterns for modern PM systems 2025-2026. Reverse engineer and analyze: "
        "(1) Linear.app schema via API: Issue, Project, Team, Cycle, Roadmap, Initiative, Milestone. "
        "Relationships (flatten/nest), custom fields vs core, workflow/state machine, identity model. "
        "(2) Jira Cloud: Epics/Stories/Tasks/Subtasks hierarchy, Projects/Sprints/Boards/Filters, "
        "custom fields (massive flexibility - pros/cons), JQL query language insights. "
        "(3) Asana: tasks-first, nested subtasks, Projects/Portfolios/Goals, dependencies, "
        "rules/automations data model. "
        "(4) ClickUp hierarchy (Everything/Spaces/Folders/Lists/Tasks): flexibility vs complexity. "
        "(5) Notion databases: schemaless at UI, structured underneath, relation/rollup/formula fields. "
        "(6) GitHub Projects v2: Items/fields/views, Issues/PRs connection. "
        "(7) Common patterns: hierarchy (flat/nested/DAG), custom fields (EAV vs JSONB vs dedicated "
        "columns vs separate tables), workflow FSM in db vs app layer, dependencies M:N blocker/blocked_by, "
        "comments polymorphic vs per-entity, attachments, permissions (row-level vs role-based), "
        "activity/audit log (append-only vs versioned). "
        "(8) Migration patterns: schema evolution without downtime, expand-contract, "
        "Alembic vs Flyway vs dbmate vs custom, testing migrations (dump/migrate/verify). "
        "(9) For SQLite MVP to PostgreSQL: when to split tables vs add columns, indexing strategy "
        "for 1000-10000 projects / 10000-100000 tasks, full-text search (FTS5, pg_trgm), "
        "time-series data retention/archival. "
        "Cite: Linear engineering blog, Atlassian developer docs, open-source PM tools source "
        "(Plane, Taskcafe, OpenProject), database design textbooks."
    ),
    "G_Metrics_TsKP_OKR_KPI": (
        "Value-focused metrics in project management 2025-2026, deep comparison: "
        "(1) Tsennyi Konechnyi Produkt (Russian: Ценный Конечный Продукт, Valuable Final Product) - "
        "Russian management tradition adopting L. Ron Hubbard methodology (Visotsky Consulting, "
        "Gennady Tishin). Origin, definition: exchangeable product of a role/task. "
        "How Russian business formulate VFP at post (role), task, department, company level. "
        "Common mistakes and anti-patterns. Comparison with western concepts. "
        "(2) OKR (Objectives and Key Results) - Andy Grove / Google: qualitative Objective + "
        "3-5 measurable Key Results, aspirational (70% success) vs committed (100%), "
        "quarterly cadence. Books: 'Measure What Matters' (John Doerr), 'Radical Focus' "
        "(Christina Wodtke). Anti-patterns: OKRs as KPIs, top-down only, too many. "
        "(3) KPI (Key Performance Indicator): leading vs lagging, input vs output vs outcome, "
        "operational monitoring context. "
        "(4) NorthStar Metric (Sean Ellis, Amplitude): NSM Framework with 3-5 input metrics, "
        "examples (Airbnb nights booked, Facebook DAU, Spotify minutes listened). "
        "(5) Others: Jobs-to-be-Done outcomes, DORA metrics (deployment frequency, lead time, MTTR, "
        "change failure rate), Pirate Metrics (AARRR: Acquisition, Activation, Retention, Referral, Revenue). "
        "(6) For solo operator + AI team: personal KPIs (deep work hours, velocity, cognitive load), "
        "team metrics when team is agents (acceptance rate, cost per task, agent uptime), "
        "burn rate (API + subscriptions) as constraint. "
        "(7) Task-level VFP (Valuable Final Product): how to write for coding task vs outcome task, "
        "VFP vs acceptance criteria overlap, measurability (binary vs scaled). "
        "(8) Project-level: health dashboards (green/yellow/red signals), leading trouble indicators, "
        "resource burn vs value delivered. "
        "(9) Portfolio-level: portfolio velocity, throughput, health distribution, "
        "quarterly graduation/archival signals. "
        "Cite: Doerr 'Measure What Matters', Wodtke 'Radical Focus', Hubbard Management System, "
        "Visotsky Consulting materials (Russian), Linear metrics philosophy, Amplitude NSM research, "
        "Reforge blog posts on metrics, DORA State of DevOps."
    ),
    "H_Capacity_planning": (
        "Capacity planning for multi-track work by solo operators and small teams (1-5 people) "
        "with AI agents as force multipliers 2025-2026: "
        "(1) Classical capacity planning: hours available vs hours needed per sprint, "
        "focus factor (percent of ideal work time actually available, 50-70 percent for knowledge work), "
        "accounting for meetings, interruptions, context switching, research, admin. "
        "(2) Multi-track allocation: ratio models (60/30/10 or 70/20/10 client-core/R&D/learning), "
        "energy-based allocation (deep-work vs admin vs creative vs reactive), seasonal themes "
        "(one-big-thing per quarter). "
        "(3) AI agent capacity: parallelism limits (how many agents can one person supervise), "
        "queue/backlog per agent, agent-specific velocity (cheap model fast for simple, expensive slow "
        "for complex). "
        "(4) Anti-patterns: over-committing sprint planning, ignoring WIP limits (work-in-progress), "
        "context-switching tax, hidden work (research, debugging) not tracked. "
        "(5) Tooling: Motion.app / Reclaim AI (AI-scheduled), Linear Cycles capacity view, "
        "GitHub Projects milestone capacity, custom (story points * historical velocity * focus factor). "
        "(6) Prioritization under constraints: Now/Next/Later + WIP limits, Must/Should/Could/Won't (MoSCoW), "
        "ICE/RICE scoring for cross-track comparison, Cost of Delay weighted shortest job first (WSJF from SAFe). "
        "(7) Signals of overcommitment: missed sprint goals 2+ consecutive, growing 'in progress' tasks, "
        "quality regression (bugs escape, defect rate up), personal (sleep, mood, focus quality). "
        "(8) Solo + AI team specifics: human oversight limit (how much agent output per day to review), "
        "response time SLA for agent questions, batching reviews vs interleaving with new tasks. "
        "Cite: Shape Up (Basecamp), Linear Method, DHH/37signals blog, Manuel Kiessling, "
        "Cal Newport 'Deep Work', Reforge capacity planning content."
    ),
    "I_Notion_integration": (
        "Notion as a PM mirror and inbox - integration patterns and gotchas 2025-2026: "
        "(1) Notion API capabilities: databases, pages, blocks, properties. "
        "Relation and rollup properties - how they work. Formula and computed properties. "
        "Rate limits, pagination. Webhooks if available 2026. "
        "(2) Common patterns for syncing external DB with Notion: identity mapping (external_id property), "
        "conflict resolution (canonical field per property), append-only blocks for logs, "
        "rollup caching vs live query. "
        "(3) Notion as inbox for ideas: quick capture on mobile (Notion AI for voice, Fast Notion entry), "
        "tagging conventions, pull to external PM. "
        "(4) Notion as due-date canonical: date property with reminders, iCal feed for calendar sync, "
        "filtering and views. "
        "(5) Real integrations: Linear with Notion, Jira with Notion, GitHub with Notion, "
        "custom Python scripts using notion-sdk-py. "
        "(6) Anti-patterns: bidirectional sync of same property - infinite loops, "
        "over-reliance on Notion as source of truth for dev work, losing data when Notion renames properties, "
        "ignoring Notion rate limits in batch sync. "
        "(7) Our specific case: Notion DS_PROJECTS with b24_company_id and b24_contact_id, "
        "Notion DS_TASKS as inbox + due dates, SQLAlchemy with notion-sdk-py mapping strategy, "
        "handling Notion API outages (queue retries). "
        "(8) Tradeoffs vs alternatives: Obsidian, Capacities, Tana, Mem.ai for multi-database. "
        "Cite: Notion API docs, notion-sdk-py github, AI-Jason integration tutorials, Thomas Frank PM templates."
    ),
    "J_Migration_cases": (
        "Case studies of teams that built custom PM systems (2018-2026) - from internal tools to public products: "
        "(1) Linear (founder stories): started as Cal Henderson + Karri Saarinen internal tool. "
        "What schema they chose, what they pivoted. When they extracted to SaaS. "
        "(2) Height, Shortcut, Notion PM features, Hey Calendar migration trajectories. "
        "(3) Open-source PM tools: Plane (Linear-clone open source) schema and architecture, "
        "Taskcafe, OpenProject, Vikunja. "
        "(4) Internal tools that stayed internal: Basecamp's Shape Up running on their own Basecamp (meta). "
        "How Stripe / Airbnb / Netflix engineering teams track work internally (anecdotal from engineering blogs). "
        "(5) From markdown-git to database: Git-based PM tools (gitea issues, tracker, gtd.md approaches). "
        "When people give up and migrate to database. Migration patterns (CSV exports, API sync, "
        "reimplement from scratch). "
        "(6) Workflow evolution: Year 1: plaintext + manual lists. Year 2: markdown + automation scripts. "
        "Year 3: database + CLI. Year 4: + web UI. Year 5: + multi-user + SaaS. Real examples of teams at each stage. "
        "(7) Our decision points: when to add web UI (if ever), when to open-source, when to productize to clients, "
        "when to merge with existing tool instead. "
        "(8) Anti-patterns: building 'perfect' PM tool without using it, migrating too often "
        "(markdown to Notion to Linear to own-DB), not syncing new-tool back to team. "
        "Cite: Linear Changelog, Notion Engineering blog, Basecamp Signal v Noise, Stripe Press, "
        "Airbnb Engineering Medium, relevant Hacker News threads with specific URLs."
    ),
}


def launch_one(code: str, prompt: str) -> dict:
    """Запустить один research-блок в фоне (no-wait)."""
    try:
        result = subprocess.run(
            [
                "notebooklm", "source", "add-research", prompt,
                "--mode", "deep",
                "--no-wait",
                "--notebook", NOTEBOOK_ID,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "code": code,
            "returncode": result.returncode,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"code": code, "returncode": -1, "stdout": "", "stderr": "TIMEOUT"}
    except Exception as exc:
        return {"code": code, "returncode": -2, "stdout": "", "stderr": f"EXC: {exc}"}


def main():
    print(f"Launching 10 deep-research blocks into notebook {NOTEBOOK_ID}")
    print(f"Parallel workers: 5")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(launch_one, code, prompt): code for code, prompt in PROMPTS.items()}
        for fut in as_completed(futures):
            result = fut.result()
            code = result["code"]
            rc = result["returncode"]
            status = "OK" if rc == 0 else f"FAIL ({rc})"
            print(f"  [{status}] {code}")
            if rc != 0:
                print(f"    stderr: {result['stderr']}")
            if result.get("stdout"):
                # попытка вытащить research_id / task_id
                try:
                    parsed = json.loads(result["stdout"])
                    rid = parsed.get("research_id") or parsed.get("task_id") or parsed.get("id")
                    if rid:
                        print(f"    id: {rid}")
                except Exception:
                    pass
            results.append(result)

    # Сводный отчёт
    ok = sum(1 for r in results if r["returncode"] == 0)
    print()
    print(f"=== SUMMARY ===")
    print(f"Launched: {ok}/{len(PROMPTS)}")
    if ok < len(PROMPTS):
        print("FAILED blocks:")
        for r in results:
            if r["returncode"] != 0:
                print(f"  - {r['code']}: rc={r['returncode']}, stderr={r['stderr'][:200]}")

    # Сохраняем сводку в файл
    summary_path = Path(__file__).parent / "v2_launch_results.json"
    summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSummary saved to {summary_path}")

    return 0 if ok == len(PROMPTS) else 1


if __name__ == "__main__":
    sys.exit(main())
