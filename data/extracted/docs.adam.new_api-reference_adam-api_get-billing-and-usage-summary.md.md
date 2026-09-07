<!-- source: https://docs.adam.new/api-reference/adam-api/get-billing-and-usage-summary.md fetched 2026-09-07T18:00:48+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Get billing and usage summary



## OpenAPI

````yaml /openapi/v1.json get /v1/billing/summary
openapi: 3.1.0
info:
  title: Adam API
  description: >-
    Create projects, start and continue AI engineering tasks, and retrieve their
    files.
  version: 1.0.0
  contact: {}
servers:
  - description: Adam API for this documentation environment
    url: '{apiOrigin}'
    variables:
      apiOrigin:
        default: https://api.adam.new
        description: >-
          Selected automatically from the production or staging documentation
          origin.
        enum:
          - https://api.adam.new
          - https://api.staging.adam.new
security: []
tags: []
paths:
  /v1/billing/summary:
    get:
      tags:
        - Adam API
      summary: Get billing and usage summary
      operationId: getBillingSummary
      parameters: []
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetPublicApiBillingSummaryResponseDto'
        '400':
          description: Invalid request
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '401':
          description: Missing or invalid API key
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '402':
          description: Payment or subscription required
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '403':
          description: Missing API key scope
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '404':
          description: Resource not found
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '409':
          description: Request conflict
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '429':
          description: Rate limit exceeded
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
        '500':
          description: Internal API error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PublicApiErrorDto'
      security:
        - personalApiKey: []
components:
  schemas:
    GetPublicApiBillingSummaryResponseDto:
      type: object
      properties:
        plan:
          type: object
          properties:
            name:
              type: string
            status:
              type: string
              enum:
                - active
                - trialing
                - past_due
                - canceled
                - incomplete
                - unpaid
                - none
            isTrial:
              type: boolean
            interval:
              anyOf:
                - type: string
                  enum:
                    - monthly
                    - annual
                - type: 'null'
            priceCents:
              anyOf:
                - type: integer
                  minimum: 0
                  maximum: 9007199254740991
                - type: 'null'
            unlimited:
              type: boolean
            renewsAt:
              anyOf:
                - type: string
                  format: date-time
                  pattern: >-
                    ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                - type: 'null'
            cancelAtPeriodEnd:
              type: boolean
          required:
            - name
            - status
            - isTrial
            - interval
            - priceCents
            - unlimited
            - renewsAt
            - cancelAtPeriodEnd
        usage:
          type: object
          properties:
            included:
              anyOf:
                - type: object
                  properties:
                    period:
                      type: string
                      enum:
                        - daily
                        - weekly
                    remainingPercent:
                      type: number
                      minimum: 0
                      maximum: 100
                    resetsAt:
                      anyOf:
                        - type: string
                          format: date-time
                          pattern: >-
                            ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                        - type: 'null'
                  required:
                    - period
                    - remainingPercent
                    - resetsAt
                - type: 'null'
            ultra:
              anyOf:
                - type: object
                  properties:
                    period:
                      type: string
                      enum:
                        - daily
                        - weekly
                    remainingPercent:
                      type: number
                      minimum: 0
                      maximum: 100
                    resetsAt:
                      anyOf:
                        - type: string
                          format: date-time
                          pattern: >-
                            ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                        - type: 'null'
                  required:
                    - period
                    - remainingPercent
                    - resetsAt
                - type: 'null'
            topUpFundsDollars:
              type: number
              minimum: 0
          required:
            - included
            - ultra
            - topUpFundsDollars
      required:
        - plan
        - usage
    PublicApiErrorDto:
      type: object
      properties:
        error:
          type: object
          properties:
            type:
              type: string
            message:
              type: string
            requestId:
              type: string
          required:
            - type
            - message
      required:
        - error
  securitySchemes:
    personalApiKey:
      scheme: bearer
      bearerFormat: adam_pk_…
      description: >-
        A personal API key created in Adam. Send it as `Authorization: Bearer
        <key>`.
      type: http

````
