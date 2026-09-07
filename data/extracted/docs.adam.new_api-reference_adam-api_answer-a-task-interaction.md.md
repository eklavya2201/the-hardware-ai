<!-- source: https://docs.adam.new/api-reference/adam-api/answer-a-task-interaction.md fetched 2026-09-07T18:02:11+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Answer a task interaction



## OpenAPI

````yaml /openapi/v1.json post /v1/tasks/{taskId}/interactions/{interactionId}
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
  /v1/tasks/{taskId}/interactions/{interactionId}:
    post:
      tags:
        - Adam API
      summary: Answer a task interaction
      operationId: resolveTaskInteraction
      parameters:
        - name: Idempotency-Key
          in: header
          description: >-
            Required unique key. For 24 hours, retries with the same key and
            body replay the original response.
          required: true
          schema:
            type: string
        - name: interactionId
          required: true
          in: path
          schema:
            type: string
            minLength: 1
        - name: taskId
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
              $ref: '#/components/schemas/ResolvePublicApiTaskInteractionRequestDto'
            examples:
              answer:
                summary: Answer a question or approval request
                value:
                  response: Approved. Use M5 fasteners.
      responses:
        '202':
          description: ''
          content:
            application/json:
              schema:
                $ref: >-
                  #/components/schemas/ResolvePublicApiTaskInteractionResponseDto
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
    ResolvePublicApiTaskInteractionRequestDto:
      type: object
      properties:
        response:
          type: string
          minLength: 1
          maxLength: 20000
      required:
        - response
    ResolvePublicApiTaskInteractionResponseDto:
      type: object
      properties:
        resolved:
          type: boolean
        task:
          type: object
          properties:
            id:
              type: string
            title:
              type: string
            status:
              type: string
              enum:
                - queued
                - running
                - awaiting_input
                - completed
                - cancelled
                - failed
            projectId:
              anyOf:
                - type: string
                - type: 'null'
            model:
              type: object
              properties:
                slug:
                  anyOf:
                    - type: string
                      minLength: 1
                      maxLength: 64
                      pattern: ^[a-z0-9]+(?:-[a-z0-9]+)*$
                    - type: 'null'
                fast:
                  type: boolean
                effort:
                  anyOf:
                    - type: string
                      enum:
                        - low
                        - medium
                        - high
                        - xhigh
                    - type: 'null'
              required:
                - slug
                - fast
                - effort
            pendingInteraction:
              anyOf:
                - type: object
                  properties:
                    id:
                      type: string
                    type:
                      type: string
                      enum:
                        - question
                        - approval
                    prompt:
                      anyOf:
                        - type: string
                        - type: 'null'
                  required:
                    - id
                    - type
                    - prompt
                - type: 'null'
            createdAt:
              type: string
            updatedAt:
              type: string
          required:
            - id
            - title
            - status
            - projectId
            - model
            - pendingInteraction
            - createdAt
            - updatedAt
      required:
        - resolved
        - task
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
