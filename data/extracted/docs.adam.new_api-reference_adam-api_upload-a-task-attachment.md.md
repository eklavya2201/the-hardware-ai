<!-- source: https://docs.adam.new/api-reference/adam-api/upload-a-task-attachment.md fetched 2026-09-07T18:01:52+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Upload a task attachment

> Uploads bytes for a later task or message. The returned upload id is opaque, expires after 24 hours, and creates no durable file until referenced.



## OpenAPI

````yaml /openapi/v1.json post /v1/uploads
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
  /v1/uploads:
    post:
      tags:
        - Adam API
      summary: Upload a task attachment
      description: >-
        Uploads bytes for a later task or message. The returned upload id is
        opaque, expires after 24 hours, and creates no durable file until
        referenced.
      operationId: createUpload
      parameters: []
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              properties:
                file:
                  format: binary
                  type: string
              required:
                - file
              type: object
      responses:
        '201':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CreatePublicApiUploadResponseDto'
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
    CreatePublicApiUploadResponseDto:
      type: object
      properties:
        upload:
          type: object
          properties:
            id:
              type: string
            name:
              type: string
            mediaType:
              type: string
            size:
              type: number
            expiresAt:
              type: string
          required:
            - id
            - name
            - mediaType
            - size
            - expiresAt
      required:
        - upload
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
