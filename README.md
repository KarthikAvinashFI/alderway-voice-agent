# Alderway outbound home-insurance intake voice agent

**Alderway Insurance Services is a fictional company, invented for building and testing voice agents.
Any resemblance to a real business is unintended.** Every lender partner, lead, name, address and phone
number in this repository is made up, and all numbers are in the 555 range reserved for fiction.

Alderway is an insurance marketplace and agency that works with mortgage lenders and servicers. When a
borrower's loan is in process, or their policy is coming up for renewal, the lender refers them across.
This agent places the outbound call, runs the full quote intake, gets a server-side eligibility verdict,
and then either hands the caller to a licensed agent or books a callback.

It is a collection agent, so the data flows the opposite way from a servicing agent: almost nothing is
read out, and almost everything is written down.

## What is enforced in code

The prompt states these in words. These are the places they are actually enforced, so a claim about a
call can be checked rather than argued about.

- **The agent holds no form.** Every question comes from `POST /next_question`, so it cannot skip one,
  reorder them, or ask one that is already settled. `agent/state.py::ensure_askable` refuses a second ask.
- **Every answer is persisted as it is collected**, through `POST /record_answers`. A dropped call leaves
  the answers in Postgres, and the next attempt resumes from them rather than starting again.
- **Eligibility is computed server side** in `tools-api`, from the rules in `tools-api/rules.py`. The
  agent may not say anything about whether a quote is possible until `check_eligibility` has answered,
  and the only decline reason it may speak is the one the API returned. `state.py::ensure_verdict` and
  `may_say_reason` are the guards.
- **Consent gates collection.** Before the recording disclosure and an agreement to continue, the only
  askable things are who answered, whether they own the home, and those two questions themselves.
- **A refusal is recorded as a refusal**, with the words used, never left as an empty field.
- **A correction keeps the original.** Answers are updated in place and the previous value moves to
  `answer_revisions`, because an underwriter later needs what was first said.
- **A removal request is honoured immediately** and suppresses the number before anything else is said.
  Nothing may be asked afterwards.
- **No price, no advice, no statement about what is covered**, however it is asked. `agent/objections.py`
  holds a fixed deflection for each shape of that question.
- **Calling hours are the property's local hours**, not ours, checked before the dial.

## Quick start

```bash
cp .env.example .env.local          # fill in LiveKit, Deepgram and either Gemini or Vertex
docker compose up -d                # postgres and the tools API
curl -s localhost:18092/health      # {"ok":true,...}

uv sync
uv run agent/agent.py console       # talk to it through your microphone
```

`console` needs no telephony and no seeded call: it works the seeded lead named by `DEMO_LEAD_PHONE`.
Set `IGNORE_CALLING_WINDOW=1` to dial outside calling hours. It applies to the pre-dial check;
a call that is already connected is always answered, whatever the hour.

Three ways to run, all the same code path:

| command | what it does |
|---|---|
| `uv run agent/agent.py console` | local microphone and speakers |
| `uv run agent/agent.py dev` | joins a LiveKit room, reloads on edit |
| `uv run agent/agent.py start` | production worker, waits for dispatch |

## Outbound calling

Neither the room nor the console dials anybody. To place a real call, run the worker in `start` or `dev`
mode and then:

```bash
uv run agent/outbound.py --phone +16145550118      # one lead
uv run agent/outbound.py --campaign 5              # the first five in the queue
```

`outbound.py` dispatches the agent into the room first and dials second, because the moment somebody
picks up there has to be somebody there. It refuses to dial a suppressed number or one outside its local
calling window, and it checks both against the tools API rather than against a second local copy.

## The form

Eight sections, 92 fields, in `tools-api/form_intake.py` as data rather than prompt text. An intake
question is a compliance artefact: it gets reviewed, it gets changed by people who do not read Python,
and the version that was asked has to be recoverable from the record of the call.

| section | what it covers |
|---|---|
| `identity_consent` | who answered, ownership, recording disclosure, consent |
| `property` | address, occupancy, age, size, construction, layout, renovations |
| `roof_systems` | roof age and material, electrical, plumbing, heating, secondary heat |
| `protective_risk` | detectors, alarm, hydrant and station distance, pool, trampoline, dogs, home business, solar |
| `coverage_policy` | current carrier, premium, dwelling limit, deductible, escrow, expiry, lapse |
| `loss_history` | claims in five years, one block per claim, prior cancellation and why |
| `auto_bundle` | asked only on interest, then one block per vehicle and per driver |
| `disposition` | transfer now or a callback window, email and permission |

Branching is real: vacancy and letting open different questions, a pre-1960 house is asked about knob and
tube wiring, a wood stove opens its inspection questions, a pool opens the fence question, and the whole
auto section is conditional. Claims, vehicles and drivers are repeating groups, expanded per item and
addressed as `claim_year#1`, `claim_year#2`.

Enum fields carry `synonyms`, because a caller says "our main home" rather than "primary", and a form
that only accepts its own tokens makes the agent ask a question that was already answered.

## The rules

32 rules in `tools-api/rules.py`, split into 15 hard disqualifiers that end the call politely and 17 soft
flags that travel with the handover. Each returns a code, and each hard rule carries one plain sentence
the agent is allowed to say.

Three outcomes, not two. A rule over a missing or refused answer is **undecidable**, never false, so an
unsettled hard rule sends the intake to `needs_review` for a person rather than quietly passing it. A rule
whose input sits behind a closed branch names the gate as well as the value, or every clean intake would
land in review.

## Seeded leads

`db/seed.sql` gives a spread worth calling: a quotable house, one that disqualifies on its roof, one on
claims history, one outside the licensed footprint, one already suppressed so the guard against dialling
it has something to catch, and one mid-intake so resume-from-drop can be exercised without placing a call.

## Verification and reset

```bash
uv run pytest                                  # 258 unit tests
docker compose up -d
uv run pytest -m integration                   # 26 more, against real Postgres
docker compose down && docker compose up -d    # reset: the volume is kept, the seed is not re-run
```

To start genuinely clean, remove the volume this project owns and bring it back up.

Set `TOOL_TRACE=/path/to/trace.jsonl` and every tools API call the agent makes is appended there, which
is how a claim about a call ("it never re-asked an answered field", "it did not invent that reason") gets
checked rather than asserted. `TRANSCRIPT_DIR` does the same for the conversation. Both are off by
default, because a transcript is personal information and a repository is the wrong place for one.

## Production replacements

- Postgres here is a demo world. In production the leads, the suppression list and the answers belong in
  the systems that already own them, reached through the same tools API boundary.
- The suppression list is local. A real deployment has to check the national and state registries too,
  and on a schedule rather than per call.
- The eligibility rules are illustrative thresholds. Real appetite comes from the carriers and changes
  weekly, which is why they are data in one file rather than logic spread through the agent.
- Nothing here writes to a CRM, sends the quote email, or actually connects a transfer. Those are the
  three integration points a real deployment adds, and each one belongs behind the tools API.
