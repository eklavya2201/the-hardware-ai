<!-- source: https://docs.adam.new/api-reference/adam-api/create-a-project.md fetched 2026-09-07T18:01:54+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Create a project



## OpenAPI

````yaml /openapi/v1.json post /v1/projects
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
  /v1/projects:
    post:
      tags:
        - Adam API
      summary: Create a project
      operationId: createProject
      parameters:
        - name: Idempotency-Key
          in: header
          description: >-
            Optional unique key. When supplied, retries with the same key and
            body replay the original response for 24 hours.
          required: false
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreatePublicApiProjectRequestDto'
            examples:
              project:
                summary: A private project
                value:
                  description: Reduce mass while preserving the mounting envelope.
                  name: Gearbox redesign
                  visibility: private
      responses:
        '201':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetPublicApiProjectResponseDto'
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
    CreatePublicApiProjectRequestDto:
      type: object
      properties:
        name:
          type: string
          minLength: 1
          maxLength: 120
        description:
          type: string
          maxLength: 4000
        visibility:
          type: string
          enum:
            - private
            - team
        memoryScope:
          type: string
          enum:
            - project
            - workspace
      required:
        - name
    GetPublicApiProjectResponseDto:
      type: object
      properties:
        project:
          type: object
          properties:
            id:
              type: string
            name:
              type: string
            description:
              anyOf:
                - type: string
                - type: 'null'
            visibility:
              type: string
              enum:
                - private
                - team
            memoryScope:
              type: string
              enum:
                - project
                - workspace
            taskCount:
              type: number
            createdAt:
              type: string
            updatedAt:
              type: string
          required:
            - id
            - name
            - description
            - visibility
            - memoryScope
            - taskCount
            - createdAt
            - updatedAt
      required:
        - project
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
