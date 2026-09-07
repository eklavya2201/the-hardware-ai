<!-- source: https://docs.adam.new/api-reference/adam-api/update-an-automation.md fetched 2026-09-07T18:01:16+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Update an automation



## OpenAPI

````yaml /openapi/v1.json patch /v1/automations/{automationId}
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
  /v1/automations/{automationId}:
    patch:
      tags:
        - Adam API
      summary: Update an automation
      operationId: updateAutomation
      parameters:
        - name: automationId
          required: true
          in: path
          schema:
            format: uuid
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/UpdatePublicApiAutomationRequestDto'
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetPublicApiAutomationResponseDto'
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
    UpdatePublicApiAutomationRequestDto:
      type: object
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 120
        prompt:
          type: string
          minLength: 1
          maxLength: 20000
        effort:
          anyOf:
            - type: string
              enum:
                - low
                - medium
                - high
                - xhigh
            - type: 'null'
        cron:
          type: string
          minLength: 1
        timezone:
          type: string
          minLength: 1
          maxLength: 100
        runAt:
          anyOf:
            - type: string
              format: date-time
              pattern: >-
                ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
            - type: 'null'
        visibility:
          type: string
          enum:
            - private
            - team
    GetPublicApiAutomationResponseDto:
      type: object
      properties:
        automation:
          type: object
          properties:
            id:
              type: string
              format: uuid
              pattern: >-
                ^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$
            name:
              type: string
            prompt:
              type: string
            effort:
              anyOf:
                - type: string
                  enum:
                    - low
                    - medium
                    - high
                    - xhigh
                - type: 'null'
            enabled:
              type: boolean
            trigger:
              type: string
              enum:
                - schedule
                - webhook
            type:
              type: string
              enum:
                - recurring
                - one_time
            cron:
              anyOf:
                - type: string
                - type: 'null'
            timezone:
              type: string
            runAt:
              anyOf:
                - type: string
                  format: date-time
                  pattern: >-
                    ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                - type: 'null'
            nextRunAt:
              anyOf:
                - type: string
                  format: date-time
                  pattern: >-
                    ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                - type: 'null'
            triggerProvider:
              anyOf:
                - type: string
                  enum:
                    - composio
                - type: 'null'
            triggerEvent:
              anyOf:
                - type: string
                - type: 'null'
            triggerApp:
              anyOf:
                - type: string
                - type: 'null'
            triggerConfig:
              anyOf:
                - type: object
                  propertyNames:
                    type: string
                  additionalProperties:
                    $ref: >-
                      #/components/schemas/GetPublicApiAutomationResponseDto__schema0
                - type: 'null'
            taskId:
              anyOf:
                - type: string
                  format: uuid
                  pattern: >-
                    ^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$
                - type: 'null'
            visibility:
              type: string
              enum:
                - private
                - team
            lastRunAt:
              anyOf:
                - type: string
                  format: date-time
                  pattern: >-
                    ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
                - type: 'null'
            lastStatus:
              anyOf:
                - type: string
                  enum:
                    - running
                    - completed
                    - failed
                    - skipped
                - type: 'null'
            runCount:
              type: integer
              minimum: 0
              maximum: 9007199254740991
            createdAt:
              type: string
              format: date-time
              pattern: >-
                ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
            updatedAt:
              type: string
              format: date-time
              pattern: >-
                ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
          required:
            - id
            - name
            - prompt
            - effort
            - enabled
            - trigger
            - type
            - cron
            - timezone
            - runAt
            - nextRunAt
            - triggerProvider
            - triggerEvent
            - triggerApp
            - triggerConfig
            - taskId
            - visibility
            - lastRunAt
            - lastStatus
            - runCount
            - createdAt
            - updatedAt
      required:
        - automation
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
    GetPublicApiAutomationResponseDto__schema0:
      anyOf:
        - type: string
        - type: number
        - type: boolean
        - type: 'null'
        - type: array
          items:
            $ref: '#/components/schemas/GetPublicApiAutomationResponseDto__schema0'
        - type: object
          propertyNames:
            type: string
          additionalProperties:
            $ref: '#/components/schemas/GetPublicApiAutomationResponseDto__schema0'
  securitySchemes:
    personalApiKey:
      scheme: bearer
      bearerFormat: adam_pk_…
      description: >-
        A personal API key created in Adam. Send it as `Authorization: Bearer
        <key>`.
      type: http

````
