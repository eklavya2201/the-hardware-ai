<!-- source: https://docs.adam.new/api-reference/adam-api/list-tasks.md fetched 2026-09-07T18:02:02+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# List tasks



## OpenAPI

````yaml /openapi/v1.json get /v1/tasks
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
  /v1/tasks:
    get:
      tags:
        - Adam API
      summary: List tasks
      operationId: listTasks
      parameters:
        - name: status
          required: false
          in: query
          schema:
            type: string
            enum:
              - queued
              - running
              - awaiting_input
              - completed
              - cancelled
              - failed
        - name: projectId
          required: false
          in: query
          schema:
            format: uuid
            type: string
        - name: cursor
          required: false
          in: query
          description: Opaque cursor returned by the previous page
          schema:
            type: string
            minLength: 1
        - name: limit
          required: false
          in: query
          schema:
            maximum: 50
            minimum: 1
            type: integer
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ListPublicApiTasksResponseDto'
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
    ListPublicApiTasksResponseDto:
      type: object
      properties:
        tasks:
          type: array
          items:
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
        nextCursor:
          anyOf:
            - type: string
            - type: 'null'
      required:
        - tasks
        - nextCursor
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
