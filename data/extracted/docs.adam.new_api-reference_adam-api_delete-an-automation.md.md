<!-- source: https://docs.adam.new/api-reference/adam-api/delete-an-automation.md fetched 2026-09-07T18:01:50+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Delete an automation



## OpenAPI

````yaml /openapi/v1.json delete /v1/automations/{automationId}
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
    delete:
      tags:
        - Adam API
      summary: Delete an automation
      operationId: deleteAutomation
      parameters:
        - name: automationId
          required: true
          in: path
          schema:
            format: uuid
            type: string
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DeletePublicApiResourceResponseDto'
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
    DeletePublicApiResourceResponseDto:
      type: object
      properties:
        deleted:
          type: boolean
          const: true
      required:
        - deleted
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
