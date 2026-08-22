---
name: adr
description: Record an architecture decision as a numbered ADR — the context that forced a choice, the options considered, the decision, and its consequences. Use when the user asks to "write an ADR", "record this decision", "document why we chose X", or when a significant, hard-to-reverse technical choice has just been made or needs making. Not for describing the resulting system as a whole (use `architecture`) and not for choices that are cheap to reverse and carry no real trade-off.
user-invocable: true
---

# adr — why the decision was made, preserved for whoever asks later

An ADR exists to answer a question that gets asked years later by someone who wasn't there:
why is it like this? Its value is almost entirely in the parts people skip — the options
that were rejected and the downsides that were knowingly accepted. A record that lists only
the winning option and its benefits is marketing, and it will get overturned by someone who
assumes nobody thought about it.

## Workflow

1. **Check the decision is worth a record.** ADRs are for choices that are expensive to
   reverse, constrain future work, or will look arbitrary to a newcomer. Cheap, obvious, or
   easily-reverted choices don't need one, and a directory full of trivial ADRs makes the
   significant ones harder to find.

2. **Look for prior art first.** Read the existing `docs/adr/` directory. If this decision
   revisits an earlier one, the new ADR supersedes it — never edit a decided ADR to reflect
   a new conclusion, because the old reasoning is exactly what a reader needs to understand
   what changed and why.

3. **Write the context as forces, not narrative.** What constraints, requirements, and
   pressures made a choice necessary? A reader should be able to tell from the context alone
   that a decision genuinely had to be made. Include the constraints that were true *at the
   time* even if they've since lapsed — that's often the answer to "why on earth is it like
   this".

4. **Record the options seriously.** Each real alternative gets its actual strengths, not a
   strawman. If an option was rejected for a reason that could change later — a library was
   immature, a cost was prohibitive at the time — say so explicitly, because that's the
   trigger for revisiting.

5. **State the decision plainly**, in the active voice, and say who made it and when.

6. **Write consequences honestly, including the bad ones.** What becomes easier, what
   becomes harder, what new obligations this creates, and what it forecloses. The negative
   consequences are the most valuable lines in the document; an ADR with none is not
   finished.

7. **Set the status.** `Proposed`, `Accepted`, `Deprecated`, or `Superseded by ADR-NNNN`. A
   proposed ADR is a legitimate artifact — writing one is a good way to have the argument
   before committing.

## Output

Write to `docs/adr/NNNN-<kebab-title>.md` using the next unused four-digit number (adapt to
the project's existing docs layout if it differs). Keep each ADR to one decision — a record
covering three choices can't be superseded cleanly when only one of them changes.

Once written, ADRs are append-only. Correct typos freely; change conclusions by writing a new
ADR that supersedes the old one and marking the original's status accordingly.
