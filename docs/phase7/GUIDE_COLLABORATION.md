# Collaboration guide

- **Workspace:** a private group. Create one and you are its admin.
- **Roles:** *viewer* can open what is shared; *editor* can also edit items shared with edit permission and share their own work; *admin* can also invite, remove, change roles, revoke any share and see the workspace event log. The last admin cannot be removed or demoted.
- **Invite:** admin enters an email and a role. A one-time link is created (valid 7 days, stored only as a hash, usable once and only by an account with that email address). It is emailed only if SMTP is configured; otherwise copy it from the page.
- **Joining shares nothing.** Members see only items an owner has explicitly shared into that workspace. Viewers can never edit, whatever permission the share has.
- **Share:** the *owner* of a report, registered model, research result, mock exam or analysis shares it with a workspace (view, or edit) and may set an expiry. Datasets, documents and conversations are shareable by description only.
- **Stop sharing / remove member:** takes effect on the next request. Schedules that depended on revoked access become *Blocked*.
- **Not found, not forbidden:** if you have no access, PredictAI answers "not found" so existence is not revealed.
- **Public sharing is off.** Nothing can be opened without signing in as a member.
- **Platform owner vs workspace role.** Being the PredictAI owner does not give access to any workspace or any user's content.
