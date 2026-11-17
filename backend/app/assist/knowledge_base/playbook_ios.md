# iOS and iPadOS mitigation playbook
## iOS: apply security updates (patching)
Settings > General > Software Update, and enable Automatic Updates including "Security Responses & System Files". Apple notes when a bug "may have been exploited" - install those updates the same day. WebKit bugs affect every browser on iPhone because all of them use WebKit, so only the iOS update fixes them. Devices too old for the current iOS get few fixes.
## iOS: spyware and targeted zero-click attacks
Mercenary spyware uses zero-click bugs in iMessage, WebKit or image parsing. Mitigation: update immediately, enable Lockdown Mode (Settings > Privacy & Security > Lockdown Mode) if you may be targeted, restart the phone daily (many implants do not survive a reboot), and take Apple threat notifications seriously. If you receive one, keep the device, enable Lockdown Mode and contact a digital-security helpline such as Access Now.
## iOS: phishing, profiles and account theft
Never install a configuration profile or enterprise app from a link, do not approve unexpected Apple ID sign-in prompts, and check Settings > General > VPN & Device Management for unknown profiles. Use a strong Apple ID password with two-factor authentication and Stolen Device Protection. If the Apple ID is taken over: reset the password at iforgot.apple.com, remove unknown devices, and review trusted phone numbers.
## iOS: hardening baseline
Automatic updates on; 6-digit or longer passcode with Face ID / Touch ID; Find My on; Stolen Device Protection on; iCloud Advanced Data Protection if possible; review app permissions for location, photos, microphone; do not jailbreak.
