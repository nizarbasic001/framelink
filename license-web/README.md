# FrameLink online license admin

The Flask service issues signed codes and provides the activation endpoint used by FrameLink. On first redemption, it binds a code to a SHA-256 hash of the PC MAC; later activations on another PC are rejected. The raw MAC is received over HTTPS but only its hash is stored. The database must persist across service restarts.

## Deploy on Render

1. Put the `license-web` folder in a private Git repository and connect the repository to Render as a Web Service.
2. Set the root directory to `outputs/license-web` (or move this folder to the repository root).
3. Set `FRAMELINK_SIGNING_KEY` to the existing 64-character hex signing key and set a long random `FRAMELINK_ADMIN_TOKEN`. Keep both secret. Set `DATABASE_URL` to a persistent PostgreSQL database. Free Render Postgres expires after 30 days, so choose persistent storage for real customer licenses.
4. Deploy and open the HTTPS URL, enter the admin token, and generate codes. The FrameLink activation endpoint is public; the admin code-generation route requires the token.
5. Put the deployed HTTPS service URL in `license-server.txt` beside FrameLink.exe before giving the app to customers.

Keep the signing key and admin token private and use HTTPS. The service stores only a SHA-256 hash of each MAC address. The customer app needs internet access during first activation; after that, it checks the signed activation locally. The device MAC can change when network hardware or its configuration changes.
