# 📋 TODO – Geplante Features & Offene Aufgaben

Stand: v0.5.6 (2026-03-01)

---

## 🚀 High Priority (nächste Releases)

### Multi-Device SSH-Key-Installation (v0.5.7)
- [ ] `POST /api/ssh/install-all` – parallelisieren statt sequenziell
- [ ] Progress-Tracking: "5/20 Geräte fertig"
- [ ] Retry-Mechanismus: Bei Fehler nächsten versuchen
- [ ] UI: Fortschrittsbalken statt Log-Wall
- Est. Aufwand: 4h

### Route-Definitionen bereinigen (v0.5.7)
- [ ] Doppelte @app.post() Routes entfernen (dead code)
  - `/api/direct-push` ×2
  - `/api/batch-push` ×2
  - `/api/config-pull/{pull_id}/save-project` ×2
  - `/api/config-pull/{pull_id}/save-template` ×2
- [ ] Code Review: Ensure first-match ist intended
- Est. Aufwand: 2h

### save-template Completeness (v0.5.7)
- [ ] `_wlans_to_uci_template()` – Network-Block hinzufügen
- [ ] System-Block hinzufügen (sysctl, firewall basics)
- [ ] Templates aus Config-Pull werden komplette UCI-Scripts
- [ ] Unit-Tests für Template-Rendering
- Est. Aufwand: 6h

### HTTPS & Production-Ready (v0.5.8)
- [ ] Reverse-Proxy-Dokumentation (nginx/caddy)
- [ ] CORS-Headers für JavaScript-Clients
- [ ] Rate-Limiting für /api/claim (Brute-Force-Protection)
- [ ] Cookie-Session-Management (statt nur HTTP Basic Auth)
- [ ] Docker-Image erstellen
- Est. Aufwand: 8h

---

## 🎯 Medium Priority (später)

### Web-UI Verbesserungen
- [ ] Dark-Mode (System-Präferenz respektieren)
- [ ] Mobile Responsive Design (Tablets testen)
- [ ] Icon-Set modernisieren (Emoji → SVG Icons)
- [ ] Keyboard-Navigation/Accessibility (WCAG 2.1)
- [ ] Multi-Language-Support (Englisch, Deutsch)
- Est. Aufwand: 12h

### Monitoring & Logging
- [ ] Syslog-Support (activity-log zu syslog-Server)
- [ ] Prometheus-Metrics (Jobs, Devices, Errors)
- [ ] Structured JSON Logging (statt inline)
- [ ] Email-Alerts bei Device-Fehler
- Est. Aufwand: 10h

### Advanced SSH
- [ ] ECDSA/Ed25519-Keys unterstützen (nicht nur RSA)
- [ ] Key-Rotation-Policy (nach X Tagen neue Keys)
- [ ] Key-Backup (verschlüsselt exportieren)
- [ ] SSH-Jumphost-Support (für Geräte hinter NAT)
- Est. Aufwand: 8h

### Automation & APIs
- [ ] Webhooks: Push-Events an externe Services
- [ ] GraphQL API (zusätzlich zu REST)
- [ ] Bulk-Upload: CSV → Geräte-Liste importieren
- [ ] Scheduled Deployments (cron-style)
- Est. Aufwand: 16h

---

## 🔧 Low Priority (optionale Extras)

### Database
- [ ] PostgreSQL Support (statt nur SQLite)
- [ ] Migration-Tool (SQLite ↔ Postgres)
- [ ] Backups (automated, encrypted, off-site)
- Est. Aufwand: 10h

### Performance & Scalability
- [ ] Config-Pull: Parallel SSH Sessions (aktuell sequenziell)
- [ ] Batch-Push: Chunked Uploads für große configs
- [ ] Database: Index-Optimization für 1000+ Geräte
- [ ] Caching: Redis für Session-Management
- Est. Aufwand: 12h

### UI/UX
- [ ] Drag-Drop für Device-Zuordnung zu Projekten
- [ ] Real-time Notifications (WebSocket statt Polling)
- [ ] Config-Diff-Viewer (visueller Vergleich)
- [ ] Template-Builder (UI statt Manual Code)
- Est. Aufwand: 20h

### Documentation
- [ ] API-Dokumentation (SwaggerUI / OpenAPI)
- [ ] Architecture-Decision-Records (ADRs)
- [ ] Troubleshooting-Guide
- [ ] Video-Tutorials
- Est. Aufwand: 8h

---

## 🐛 Known Issues (nicht blockierend)

### Code Quality
- [ ] Doppelte Route-Definitionen (v0.5.7 Cleanup)
- [ ] Type-Hints nicht vollständig (mypy Checks)
- [ ] Error-Messages manchmal kryptisch
- [ ] Logging ist inconsistent (mix aus inline strings)

### Features Gaps
- [ ] `save-template` nur WLAN-Block (kein Network/System)
- [ ] Keine Token-Rotation (statisch aus env var)
- [ ] Keine Device-Gruppen (nur Projects)
- [ ] Keine Rollback-Mechanismus nach Push

### Performance
- [ ] Config-Pull sequenziell statt parallel SSH
- [ ] Activity-Log limitiert auf 100 Einträge (in RAM)
- [ ] Kein Caching für häufig abgerufene Configs
- [ ] Batch-Push wartet auf alle Devices (keine Async-Waits)

### Security
- [ ] HTTP-only (kein HTTPS)
- [ ] Basic Auth statt OAuth/OIDC
- [ ] SSH-Private-Key in Plain-Text in DB
- [ ] No encryption at rest für provision.db
- [ ] Token-Bruteforce nicht rate-limited

---

## ✅ Completed (v0.5.0 – v0.5.6)

### Bootstrap & Provisioning
- ✅ v0.4.0: Bootstrap-Script grundlegend
- ✅ v0.4.5–v0.4.9: Bootstrap deterministisch & fail-fast
- ✅ v0.5.0: Logging, HTTP-Status, Fehlerseiten-Check
- ✅ v0.5.1: fail-fast, json_escape(), curl Fallback
- ✅ v0.5.2: uci_cmds Array→String, global exception handler
- ✅ v0.5.3: SWITCH_BLOCK, network restart handling
- ✅ v0.5.4: Export/Import, Device-Vorregistrierung, save-project bug
- ✅ v0.5.5: Debug-Dashboard, Activity-Logging
- ✅ v0.5.6: SSH-Key-Generator, Auto-Installer

### Features
- ✅ Template-System mit {{VAR}}-Ersetzung
- ✅ Multi-WLAN pro Projekt
- ✅ VLAN/Switch-Config-Generation
- ✅ Config-Pull/Push UI
- ✅ SSH-Deploy (Direct + Batch)
- ✅ Device-Discovery (LuCI-Scan)
- ✅ Diagnose-Reports
- ✅ Projekt-Management
- ✅ Template-Versioning
- ✅ SSH-Key-Verwaltung (Upload)
- ✅ Gerät-Vorregistrierung
- ✅ Export/Import Backup
- ✅ Live-Debug-Dashboard
- ✅ SSH-Key-Generator & Auto-Installer

---

## 📊 Estimation Summary

| Phase | Features | Aufwand | Priorität |
|---|---|---|---|
| v0.5.7 | Multi-SSH, Cleanup, save-template | 12h | 🔴 HIGH |
| v0.5.8 | HTTPS, Production-Ready, Docker | 8h | 🔴 HIGH |
| v0.6.0 | UI/UX, Mobile, Monitoring | 22h | 🟡 MEDIUM |
| v0.7.0 | Advanced Features, Automation | 24h | 🟡 MEDIUM |
| v1.0.0 | Final Polish, Docs, Testing | 16h | 🟢 LOW |

**Gesamtaufwand bis v1.0.0**: ~80–100 Stunden (2–3 Monate @ 10h/Woche)

---

## 🎯 Roadmap

```
v0.5.6 (CURRENT)
├─ SSH-Generator ✅
├─ Activity-Logging ✅
└─ Debug-Dashboard ✅

v0.5.7 (Q1 2026)
├─ Multi-SSH parallelisieren
├─ Route-Cleanup
└─ save-template completeness

v0.5.8 (Q1 2026)
├─ HTTPS + Production-Ready
├─ Docker-Image
└─ Rate-Limiting

v0.6.0 (Q2 2026)
├─ Modern UI (Dark-Mode, Mobile)
├─ Monitoring (Prometheus, Email-Alerts)
└─ Advanced Features

v1.0.0 (Q3 2026)
├─ Final Polish
├─ Full Documentation
└─ Stable Release
```

---

## 💡 Contribution Guidelines

1. **Bug Reports**: Issue mit `[BUG]` Prefix
2. **Feature Requests**: Issue mit `[FEATURE]` Prefix
3. **PRs**: Gegen `main` branch, mit Commit-Messages im Format `v0.x.x: Beschreibung`
4. **Testing**: Alle API-Changes sollten mit curl-Examples dokumentiert sein
5. **Docs**: README.md + CHANGELOG.md updaten

---

## 📞 Contact

GitHub: [magicx78/openwrt](https://github.com/magicx78/openwrt) (privat)

---

**Zuletzt aktualisiert**: 2026-03-01
**Version**: v0.5.6
**Status**: ✅ Stable, Production-Ready (mit HTTP-Limitation)
