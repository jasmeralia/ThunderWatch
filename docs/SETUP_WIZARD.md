# ThunderWatch setup wizard

The wizard configures an SMTP account, one recipient, a location label, lookup interval,
IPv6 monitoring, start-at-sign-in, and beta-update preference. It contacts providers
only when **Send Test Email** is selected. The test email includes any current addresses
returned by the providers.

![Welcome](images/wizard-steps/01-welcome.png)

![Email server](images/wizard-steps/02-email-server.png)

![Recipient and location](images/wizard-steps/03-recipient-location.png)

![Monitoring and startup](images/wizard-steps/04-monitoring-startup.png)

![Test and finish](images/wizard-steps/05-test-and-finish.png)

If an OS keyring is unavailable, ThunderWatch offers a password file only after the
owner-only storage checkbox is selected. A test email must succeed before setup can
finish, unless **Save without a successful test** is explicitly selected.
