# Your own OAuth app

mcpMail does not include a client ID or client secret. Everyone installing from
GitHub must register their own app. A client ID identifies the app; it is not
your email password. Never share tokens, passwords or downloaded client files in Git.

## Gmail

1. Create or select your own project in [Google Cloud Console](https://console.cloud.google.com/).
2. Open Google Auth Platform. Complete Branding and select External under Audience.
   In Testing, add your Gmail addresses as Test users.
3. Create an OAuth client with the type **Desktop app**, not Web application or service account.
4. Copy your client ID and client secret from this registration. Keep any
   downloaded JSON outside the project directory.
5. Open **Account > OAuth app settings** in mcpMail. Select Gmail, enter
   both values and save. They are stored in your keyring.
6. Add your Gmail account, select OAuth2, and complete sign-in in the browser.
   Grant email access via `https://mail.google.com/`.

The client uses a local loopback callback with PKCE. Testing may require you
to sign in again after a refresh token expires. This setup is for your own use,
not a verified app registration shared with others.
See [Google's guide for desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app).

## Personal Outlook / Hotmail

1. Create your own app under **App registrations > New registration** in
   [Microsoft Entra](https://entra.microsoft.com/).
2. Select support for personal Microsoft accounts; the option that includes
   both organizational and personal accounts can also be used.
3. Under Authentication, add **Mobile and desktop applications**
   with the redirect URI `http://localhost/oauth/callback`. Do not select Web or SPA.
   The app chooses an available local port; the port is not fixed in the registration.
4. Use the delegated Exchange Online permissions **IMAP.AccessAsUser.All** and
   **SMTP.Send**. The app also requests `offline_access`. Do not use application
   permissions or Microsoft Graph Mail.Read for this IMAP/SMTP client.
5. Copy **Application (client) ID**, not Object ID or Directory ID.
   Save it under **Account > OAuth app settings > Outlook**.
   Leave the client secret empty for this public desktop client.
6. Add the account and sign in with OAuth2. IMAP must be allowed in the account's
   email settings.

See Microsoft's [app registration guide](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app),
[IMAP/SMTP-scopes](https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth)
and [loopback rules](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url).
School/work accounts may require administrator approval or block the protocols;
your own client ID does not bypass these restrictions.

## Environment variables and troubleshooting

Alternatively, set your values in `MAILKLIENT_GMAIL_CLIENT_ID`,
`MAILKLIENT_GMAIL_CLIENT_SECRET` and `MAILKLIENT_OUTLOOK_CLIENT_ID`.
The app does not read `.env` automatically. Environment variables take precedence
over the keyring; remove old `export` lines to use the values saved in the app.
An overridden client ID is never automatically paired with another app's stored secret.

Missing client ID: save your own app settings for the selected provider.
For `redirect_uri_mismatch`: check the platform and callback path.
For `access_denied`: check the test user, account type, consent and organization policy.
For keyring read/write problems: unlock the keyring and use a regular logged-in
desktop session. Do not switch to an unencrypted file backend for passwords.
