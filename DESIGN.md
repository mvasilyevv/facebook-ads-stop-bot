<!-- SEED: established with the user before implementation; re-run $impeccable document once there's code to capture the actual tokens and components. -->
---
name: FB Agent
description: A dark precision ledger for safety-first iGaming operations.
---

# Design System: FB Agent

## Overview

**Creative North Star: "Точный журнал"**

FB Agent looks like a financial reconciliation instrument under night-shift light: calm, exact, dense without becoming cramped. Every value belongs to a ruled record with a visible relationship to its source, timestamp, threshold, or confirmed state. The material character comes from registration proofs, audit marks, fine ledger rules, and restrained brass notation rather than dashboard chrome.

The world is dark-first on web and always dark in TMA. Desktop and mobile share the same hierarchy and evidence, but mobile recomposes rows into vertical ledger strips rather than shrinking a table. Movement is reserved for state transitions, current-time progress, reconciliation, and newly arrived evidence; decoration never moves on its own.

**Key Characteristics:**

- Flat charcoal fields with warm paper-like text and hairline ruled structure.
- One continuous audit scale for actual, base, stop, and current time.
- Registration marks, stamps, and state glyphs used as semantic proof.
- Compact, legible density with a calm visual pulse.
- No illustrative imagery; the operating record itself is the visual material.

## Colors

The palette is restrained: near-black graphite carries the surface, warm paper carries reading, aged brass marks structure and pending work, and muted red appears only for active danger or failed money-actions.

### Primary

- **Aged Brass** (`#B8A36A`): active navigation, current-time marks, selected controls, pending/degraded emphasis, and audit registration details.

### Secondary

- **Ledger Danger** (`#C9554D`): critical state, breached stop threshold, destructive confirmation, and failed money-action only.

### Neutral

- **Night Graphite** (`#0B0D10`): the uninterrupted application ground.
- **Warm Ledger Paper** (`#E7E1D5`): primary text and confirmed high-contrast values.
- **Ruled Ink** (`#34383A`): dividers, inactive plotting marks, and structural outlines.
- **Unknown Ash** (`#858A8D`): unknown, unavailable, and intentionally absent values.

### Named Rules

**The Proof-Color Rule.** Color is evidence, never decoration: green may appear only for confirmed success, red only for active danger, brass for pending or degraded, and ash for unknown or stale.

**The One-Ground Rule.** The app uses one continuous graphite field; sections are separated by rules, alignment, and density shifts rather than stacks of elevated cards.

## Typography

**Display Font:** **Commissioner** — a compact Cyrillic workhorse grotesk with enough character for route and ledger headings without reading like marketing typography.

**Body Font:** **Commissioner** — one variable family carries sustained UI copy, labels, and controls so hierarchy comes from optical size and weight rather than extra font downloads.

**Label/Mono Font:** **JetBrains Mono** — restricted to money, ratios, timestamps, task numbers, cabinet identifiers, and other measured values with clear glyph differentiation.

**Character:** Functional and measured. Headlines establish a register, body text explains the decision, and numbers align like ledger entries; tracked all-caps is limited to short status stamps.

### Hierarchy

- **Headline:** compact and firm, used once per route to name the operator context.
- **Title:** used for ledger sections and cabinet rows, never as decorative card headings.
- **Body:** the default reading voice; no explanatory copy below 16px.
- **Secondary:** 14px for supporting context and metadata.
- **Label:** minimum 12px for terse service labels; never used for essential actions or explanations.

### Named Rules

**The Tabular Proof Rule.** Money, ratios, timestamps, and task numbers use tabular numerals and preserve their decimal alignment.

## Layout

The spatial grammar is a ruled ledger: stable columns and shared scales on desktop, vertical evidence strips on mobile. The first viewport prioritizes freshness and health, ranked attention, spend/base/stop, the short funnel, and current action lifecycle. Whitespace is measured in row rhythm, not empty card gutters.

Desktop uses a compact navigation rail plus a wide evidence canvas: the shared portfolio scale leads, ranked attention occupies the first right rail, the funnel follows the scale, and action lifecycle follows attention. Mobile uses bottom navigation, safe-area-aware sticky actions, and one-column sections ordered attention → scale → actions → funnel without horizontal page scrolling. Touch targets are at least 44×44px.

## Elevation & Depth

The system is flat. Depth comes from tonal fields, line weight, overlap only where a sheet or dialog is physically above the ledger, and localized focus treatment. Resting cards do not cast shadows; dialogs and mobile sheets may use one restrained ambient shadow to clarify modality.

### Named Rules

**The Flat Record Rule.** A resting data surface never floats. If a shadow is visible, the element must be transient, modal, or actively manipulated.

## Shapes

Corners are precise and only gently eased. Rows, controls, and containers use near-square geometry; round pills are reserved for truly compact filters or binary states. Hairline rules dominate, while 2px strokes are reserved for active focus, threshold breach, or selected registration marks.

## Do's and Don'ts

### Do:

- **Do** align values to shared baselines and scales so comparisons happen without rereading labels.
- **Do** pair every status color with an icon and text label.
- **Do** use registration marks, checks, crosses, and ruled lines as semantic state grammar.
- **Do** preserve the same evidence and action lifecycle across desktop, mobile web, and TMA.

### Don't:

- **Don't** build a wall of independent KPI cards or repeat the same degraded reason in every section.
- **Don't** use glass, glow, neon, glossy gradients, or decorative shadows.
- **Don't** compress a desktop table into a horizontally scrolling mobile table.
- **Don't** use red, green, or brass without a state meaning.
- **Don't** animate stable values, background ornament, or layout merely to make the interface feel active.
