# Payments

**Status:** implemented (tenant plane).

Invoices and their payment status. **No card or bank data is ever stored** —
payments are recorded amounts received (cash/transfer).

## Lifecycle (enforced in the domain)

```
DRAFT ──issue──> ISSUED ──record_payment(...)──> PAID (when fully settled)
  │                 │
  └──void(reason)───┴──> VOID
```

Payments can only be recorded against an issued invoice, must match its
currency, and cannot exceed the outstanding balance. Paid invoices are terminal.
An optional linked customer/deal is verified to exist in the same tenant.

## Events

`payments.invoice_issued`, `payments.payment_received`, `payments.invoice_paid`,
`payments.invoice_voided` — written to the outbox in the same transaction.

## API

`GET/POST /api/v1/payments/invoices/`, `GET /api/v1/payments/invoices/{id}/`,
`POST .../{id}/issue|payments|void/`. Tenant-scoped, RBAC (`payments.*`,
own/team/all scope).
