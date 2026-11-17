# Mitigation by threat type
## What to do about an actively exploited vulnerability
An Active threat is being used by attackers right now. Order of work: 1) find every affected system (version check). 2) Patch within the deadline - CISA gives federal agencies about three weeks for KEV entries; internet-facing systems should be patched in days. 3) If patching must wait, apply the vendor workaround or take the system off the internet. 4) Hunt for signs that the bug was already used against you. 5) Confirm the fix (version check, rescan) and record it.
## What to do about an evolving threat
An Evolving threat is changing: a proof-of-concept exploit was published or sightings are rising, so exploitation usually follows within days. Schedule the patch now rather than at the next cycle, add detection rules for the published exploit, and watch the Kavach feed - when exploitation is confirmed its status becomes Active.
## What to do about an anticipated threat
An Anticipated threat is not exploited yet but is severe or widely discussed. Add it to the next regular patch cycle, check whether you run the affected product at all, and make sure the product is not needlessly exposed to the internet. No emergency action is needed.
## What to do about a resolved threat
A Resolved threat has a fix and no current sightings. Verify that the fix is really installed everywhere (old vulnerabilities are still exploited on unpatched machines), then close it. Keep the detection rule.
## Ransomware response procedure
Isolate affected machines, do not switch them off, identify the ransomware family from the note, preserve logs, check whether backups are intact and offline, reset credentials, remove the attacker's access (patch the entry point: usually VPN, RDP or phishing), restore from clean backups, and report to the authorities. Check nomoreransom.org for decryptors before considering anything else.
## Phishing and credential theft response procedure
Reset the password, sign out all sessions, enable MFA (prefer an authenticator app or passkey), check mailbox rules and forwarding that the attacker may have added, warn contacts, and report the phishing page. For payment fraud call the bank at once.
## Supply-chain and malicious package incidents
If a dependency or vendor update was malicious: pin the last known good version, remove the bad one, rotate every secret the build or host could read, rebuild from clean sources, and check outbound connections made during the exposure window.
## General incident response steps
Preparation, identification, containment, eradication, recovery, lessons learned. Write down the time line while you work, keep evidence, communicate early, and after recovery fix the root cause so the same path cannot be used again.
