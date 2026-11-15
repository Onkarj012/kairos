# 34: Stage 4: trades by itself within limits

**What to build:** The system places its own orders within the limits and the loss budget, and capital is raised in steps. Each raise needs the previous level to have stayed inside its predicted range, never just a good month. Upkeep:
- retrain monthly or quarterly with the newest data;
- a new model runs on paper beside the current one for at least 20 trading days and must beat it before taking over;
- every month from 2027 onward is kept as fresh blind data.

Plan: `../spec.md` sections 10 and 11.

**Blocked by:** 33

**Status:** ready-for-human

- [ ] Promotion to self-trading needs the owner's explicit sign-off. Drop-back is automatic on the triggers from ticket 16.
- [ ] Each capital raise is recorded with the evidence it rests on.
- [ ] The 20-day side-by-side rule is enforced in code before a model swap.
