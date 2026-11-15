# 33: Stage 3: Rs 10,000, owner approves each trade

**What to build:** The first real money. Rs 10,000 per style that has passed. The owner approves or rejects each trade on the local approvals page before it is sent. The stage runs 8 to 12 weeks and checks that real fills match paper fills and that costs match the cost model. Stage 3 remains owner-controlled and may overlap with paper stage 2 only after every explicit per-style promotion gate is met. Common prerequisites are tickets 30, 31, and 32. Swing additionally requires its own accepted paper evidence from ticket 16; same-day additionally requires its own accepted paper evidence from ticket 22. Do not require ticket 22 for swing, ticket 16 for same-day, or both records globally. Ticket 22 acceptance requires its own 3–4-week shakedown and about 100 completed trades with its own passed scorecard; paper code alone is not evidence. Stage 3 also requires its own blind pass and owner approval. Plan: `../spec.md` section 10, stage 3.

**Blocked by:** 30, 31, 32 (plus the matching style's accepted paper evidence: swing 16 or same-day 22)

**Status:** ready-for-human

- [ ] Every order needs an explicit approval, and an unanswered pick is not sent.
- [ ] Every session ends with account and broker matching. Any mismatch drops the style back to paper.
- [ ] The real-versus-paper fill gap and the real-versus-modelled costs are on the scorecard.
- [ ] The loss budget is live, and a 10% stop returns the style to paper until the owner restarts it.
