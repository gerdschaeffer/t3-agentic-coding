---
name: technical-spec-writing
description: >-
  Write and review rigorous technical specification documents — RFCs, design
  docs, protocol specs, ADRs, and API specs. Use this skill whenever the user
  asks to draft, write, edit, or review an RFC, design doc, spec, protocol,
  standard, technical proposal, or "how should this system work" document, and
  also whenever a document needs precise normative requirements (MUST/SHOULD),
  wire formats, state machines, or an "alternatives considered" analysis — even
  if the user doesn't say the word "RFC." Prefer this over generic prose writing
  whenever the goal is a document an engineer could implement from without
  asking follow-up questions.
---

# Technical Specification Writing

## What this is for

The reader of a spec is an implementer. Your goal is a document they can build
from without coming back to ask what you meant. When precision and readability
conflict, precision wins — a spec that reads beautifully but leaves a field's
encoding ambiguous has failed at its one job. Everything below serves that goal.

Before writing, know which genre you're in, because the emphasis shifts:

- **RFC / protocol spec** — normative requirements, wire formats, and interop
  are the point. Be exhaustive about edge cases and error handling.
- **Design doc / ADR** — the *decision* and its *rationale* are the point.
  Alternatives-considered and trade-offs carry more weight than byte layouts.
- **API spec** — types, contracts, status codes, and examples dominate.

If it's unclear which the user wants, ask once, then proceed.

## Structure

Lead with a short abstract: two to four sentences stating what this proposes and
why it exists. A reader should be able to decide from the abstract alone whether
this document is relevant to them.

Then follow a predictable spine so reviewers always know where to look:

1. Context / Motivation — the problem, stated before any solution
2. Goals and Non-Goals
3. Specification / Proposal — the substance
4. Alternatives Considered
5. Security & Privacy Considerations
6. Open Questions

Number the sections. Reviewers cite specs by section ("re: §3.2"), and numbering
is what makes that possible. State the problem fully before introducing the
solution — a reader who doesn't understand the problem can't evaluate the fix.

## Requirement language

Use RFC 2119 keywords deliberately: MUST, MUST NOT, REQUIRED, SHOULD, SHOULD
NOT, MAY. Capitalize them when they carry normative weight, and add one sentence
early on noting you follow this convention. The reason capitalization matters:
implementers scan for the capitalized words to build their conformance
checklist, so a lowercase "should" reads as commentary and a "SHOULD" reads as a
graded requirement. Don't inflate a recommendation to MUST, and don't soften a
hard requirement to SHOULD — the keyword *is* the contract.

## Precision

This is where specs live or die.

Define every term before you use it. If there are more than a handful, add a
Terminology section up front. A term used before it's defined forces the reader
to guess or scroll.

Specify exact types, units, ranges, encodings, and byte order. "A number" is a
bug; "unsigned 32-bit integer, big-endian, range 1–65535" is a spec. State the
default for every optional value and say explicitly what happens when a value is
absent, malformed, or out of range. Unspecified edge cases are where
implementations silently diverge and interop breaks, so treat an unhandled edge
case as a defect in the document, not a detail to leave to the reader.

Make error behavior first-class. For each operation that can fail, say how it
fails, what the caller observes, and whether the failure is recoverable.

## Rationale and non-goals

State what is explicitly out of scope. Non-goals prevent scope creep in review
and stop implementers from building things you never intended.

When you make a design choice, name the alternatives you rejected and say why.
The reader's very first reaction to any decision is "why not X?" — answering it
in the document, before they ask, is what separates a spec people trust from one
they argue with. This matters most in design docs and ADRs, where the reasoning
*is* the deliverable and a future engineer will read it specifically to
understand why the system is the way it is.

## Style

Write in prose and short paragraphs. Reserve lists for genuinely enumerable
things — field definitions, ordered steps, option sets — because a list implies
its items are parallel and complete, and using one for flowing argument sends a
false signal. Prefer active voice and present tense: "The server rejects the
request" states a rule; "The request will be rejected" hides who acts and when.

Be terse. If a sentence adds neither a constraint nor a reason for one, cut it.
No marketing tone, no hedging, no throat-clearing. Every sentence should either
tell the implementer what to do or explain why.

## Examples and diagrams

Include at least one concrete worked example for every non-trivial mechanism: a
sample message, a request/response pair, a filled-in wire format. Examples catch
ambiguities that prose hides, because you can't write a concrete example of an
underspecified rule. Put anything machine-relevant in fenced code blocks.

Use diagrams for anything with structure over time or space — state machines,
sequences, packet layouts. ASCII art or Mermaid both work; the point is that a
reader shouldn't have to reconstruct a state machine from paragraphs of prose.

Examples are illustrative, not normative. If there's any risk a reader mistakes
an example for a requirement, say so.

## Rigor and honesty

Flag your assumptions rather than burying them. When something is undecided,
mark it clearly as an Open Question — a visible unknown is far safer than a
confident-sounding sentence that papers over a gap. Never invent a detail to
make the document feel finished; a spec that sounds complete but isn't is more
dangerous than one that admits what's unresolved, because implementers trust it.

Include a Security & Privacy Considerations section even when the honest answer
is "none identified" — say that, and say why, so reviewers know it was
considered rather than forgotten. When changing existing behavior, add
backward-compatibility and migration notes: what breaks, who's affected, and the
upgrade path.

Keep binding requirements separate from commentary. When you need to explain
reasoning inline, set it off (for example, a "Rationale:" note) so implementers
can tell at a glance what is conformance-relevant and what is context.

## Quick self-check before delivering

- Could an implementer build this without asking you a question? If not, what's
  missing is your next paragraph.
- Is every MUST/SHOULD used deliberately, and every field's type, units, and
  default specified?
- Does every non-trivial mechanism have a worked example?
- Are the rejected alternatives and their reasons written down?
- Are open questions and assumptions visible rather than hidden?
