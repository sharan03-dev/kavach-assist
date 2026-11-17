# Web and server software mitigation playbook
## Web and server software: patch vulnerable applications and libraries
For a vulnerable server product (Apache, Nginx, Tomcat, Confluence, Exchange, VMware, WordPress plugin, Log4j, OpenSSL): upgrade to the fixed version named in the advisory and restart the service. For libraries inside your own code, bump the dependency: Python `pip install -U <package>` and pin the fixed version in requirements.txt; Node `npm audit fix` or set the fixed version in package.json; Java update the Maven / Gradle version. Rebuild and redeploy containers - a patched host does not patch the image. The Kavach scanner bot finds such vulnerable dependencies and can apply the version bump after you confirm in chat.
## Web and server software: virtual patching when you cannot upgrade yet
Put the application behind a web application firewall rule that blocks the exploit pattern, restrict the vulnerable endpoint to trusted IPs, disable the vulnerable feature or plugin, and monitor logs for exploit attempts. These reduce risk but the upgrade is still required.
## Web and server software: after exploitation (web shells and data theft)
Search the web root for recently changed or unknown files (`find /var/www -mtime -7 -type f`), check for new scheduled jobs and admin users, rotate database passwords, API keys and session secrets, invalidate sessions, review access logs for the first exploit request to learn what was read, restore from a clean build, and tell affected users if data left.
## Web and server software: browsers (Chrome, Edge, Firefox)
Browser zero-days are fixed by updating and restarting the browser: Chrome menu > Help > About Google Chrome > Relaunch. The update only takes effect after the restart. Remove extensions you do not need.
## Web and server software: secure coding and configuration baseline
Parameterised queries (no string-built SQL or NoSQL queries), input validation, output encoding, least-privilege database users, secrets in environment variables or a secrets manager (never in code or images), TLS everywhere, rate limiting, security headers, dependency scanning in CI, and no debug mode in production.
