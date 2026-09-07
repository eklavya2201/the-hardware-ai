<!-- source: https://docs.adam.new/api-reference/adam-api/get-authentication-context.md fetched 2026-09-07T18:00:40+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Get authentication context



## OpenAPI

````yaml /openapi/v1.json get /v1/auth/context
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
  /v1/auth/context:
    get:
      tags:
        - Adam API
      summary: Get authentication context
      operationId: getAuthContext
      parameters: []
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetPublicApiAuthContextResponseDto'
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
    GetPublicApiAuthContextResponseDto:
      type: object
      properties:
        credentialType:
          type: string
          enum:
            - personal_api_key
            - oauth_access_token
        user:
          type: object
          properties:
            id:
              type: string
            name:
              type: string
            email:
              type: string
          required:
            - id
            - name
            - email
        team:
          type: object
          properties:
            id:
              type: string
            slug:
              type: string
          required:
            - id
            - slug
        scopes:
          type: array
          items:
            type: string
            enum:
              - projects:read
              - projects:write
              - tasks:read
              - tasks:write
              - files:read
              - files:write
              - billing:read
              - integrations:read
              - automations:read
              - automations:write
      required:
        - credentialType
        - user
        - team
        - scopes
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
