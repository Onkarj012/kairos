# 32: Always-on server with a fixed internet address

**What to build:** Zerodha accepts API orders only from a fixed internet address registered on the developer profile, so real money needs an always-on Linux server with a static IP. Steps:
- the owner rents or sets up the server;
- registers its address with Zerodha;
- turns on DDPI, so selling held shares needs no per-order approval;
- sets up encrypted backups.

The daily jobs from ticket 14 move onto the server. The dashboard stays private, reached over SSH. Secrets never appear in logs. Plan: `../retained-system-requirements.md`.

**Blocked by:** 14

**Status:** ready-for-human

- [ ] The server runs the daily jobs, with the outside-the-machine alert working.
- [ ] Its static IP is registered with Zerodha, and DDPI is on.
- [ ] A restore from backup is tested once and works.
