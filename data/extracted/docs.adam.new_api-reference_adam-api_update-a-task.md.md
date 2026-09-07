<!-- source: https://docs.adam.new/api-reference/adam-api/update-a-task.md fetched 2026-09-07T18:02:06+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Update a task



## OpenAPI

````yaml /openapi/v1.json patch /v1/tasks/{taskId}
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
  /v1/tasks/{taskId}:
    patch:
      tags:
        - Adam API
      summary: Update a task
      operationId: updateTask
      parameters:
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
              $ref: '#/components/schemas/UpdatePublicApiTaskRequestDto'
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/GetPublicApiTaskResponseDto'
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
    UpdatePublicApiTaskRequestDto:
      type: object
      properties:
        title:
          type: string
          minLength: 1
          maxLength: 500
        projectId:
          anyOf:
            - type: string
              format: uuid
              pattern: >-
                ^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$
            - type: 'null'
    GetPublicApiTaskResponseDto:
      type: object
      properties:
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
        messages:
          type: array
          items:
            type: object
            properties:
              id:
                type: string
              role:
                type: string
                enum:
                  - user
                  - assistant
              parts:
                type: array
                items:
                  anyOf:
                    - type: object
                      properties:
                        type:
                          type: string
                          const: reasoning
                        text:
                          type: string
                      required:
                        - type
                        - text
                    - type: object
                      properties:
                        type:
                          type: string
                          const: text
                        text:
                          type: string
                      required:
                        - type
                        - text
                    - type: object
                      properties:
                        type:
                          type: string
                          const: file
                        id:
                          anyOf:
                            - type: string
                            - type: 'null'
                        name:
                          anyOf:
                            - type: string
                            - type: 'null'
                        mediaType:
                          type: string
                        downloadUrl:
                          type: string
                          format: uri
                      required:
                        - type
                        - id
                        - name
                        - mediaType
                        - downloadUrl
                    - type: object
                      properties:
                        type:
                          type: string
                          const: source_url
                        sourceId:
                          type: string
                        url:
                          type: string
                          format: uri
                        title:
                          anyOf:
                            - type: string
                            - type: 'null'
                      required:
                        - type
                        - sourceId
                        - url
                        - title
                    - type: object
                      properties:
                        type:
                          type: string
                          const: source_document
                        sourceId:
                          type: string
                        mediaType:
                          type: string
                        title:
                          type: string
                        filename:
                          anyOf:
                            - type: string
                            - type: 'null'
                      required:
                        - type
                        - sourceId
                        - mediaType
                        - title
                        - filename
                    - type: object
                      properties:
                        type:
                          type: string
                          const: reasoning_file
                        mediaType:
                          type: string
                        url:
                          type: string
                          format: uri
                      required:
                        - type
                        - mediaType
                        - url
                    - type: object
                      properties:
                        type:
                          type: string
                          const: data
                        name:
                          type: string
                        id:
                          anyOf:
                            - type: string
                            - type: 'null'
                        data:
                          $ref: >-
                            #/components/schemas/GetPublicApiTaskResponseDto__schema0
                      required:
                        - type
                        - name
                        - id
                        - data
                    - type: object
                      properties:
                        type:
                          type: string
                          const: custom
                        kind:
                          type: string
                        providerMetadata:
                          $ref: >-
                            #/components/schemas/GetPublicApiTaskResponseDto__schema0
                      required:
                        - type
                        - kind
                    - type: object
                      properties:
                        type:
                          type: string
                          const: step_start
                      required:
                        - type
                    - type: object
                      properties:
                        type:
                          type: string
                          const: tool_call
                        toolCallId:
                          type: string
                        name:
                          type: string
                        input:
                          $ref: >-
                            #/components/schemas/GetPublicApiTaskResponseDto__schema0
                      required:
                        - type
                        - toolCallId
                        - name
                    - type: object
                      properties:
                        type:
                          type: string
                          const: tool_result
                        toolCallId:
                          type: string
                        name:
                          type: string
                        status:
                          type: string
                          const: success
                        output:
                          $ref: >-
                            #/components/schemas/GetPublicApiTaskResponseDto__schema0
                      required:
                        - type
                        - toolCallId
                        - name
                        - status
                    - type: object
                      properties:
                        type:
                          type: string
                          const: tool_result
                        toolCallId:
                          type: string
                        name:
                          type: string
                        status:
                          type: string
                          const: error
                        error:
                          type: string
                      required:
                        - type
                        - toolCallId
                        - name
                        - status
                    - type: object
                      properties:
                        type:
                          type: string
                          const: tool_result
                        toolCallId:
                          type: string
                        name:
                          type: string
                        status:
                          type: string
                          const: denied
                      required:
                        - type
                        - toolCallId
                        - name
                        - status
              createdAt:
                type: string
                format: date-time
                pattern: >-
                  ^(?:(?:\d\d[2468][048]|\d\d[13579][26]|\d\d0[48]|[02468][048]00|[13579][26]00)-02-29|\d{4}-(?:(?:0[13578]|1[02])-(?:0[1-9]|[12]\d|3[01])|(?:0[469]|11)-(?:0[1-9]|[12]\d|30)|(?:02)-(?:0[1-9]|1\d|2[0-8])))T(?:(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z))$
            required:
              - id
              - role
              - parts
              - createdAt
      required:
        - task
        - messages
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
    GetPublicApiTaskResponseDto__schema0:
      anyOf:
        - type: string
        - type: number
        - type: boolean
        - type: 'null'
        - type: array
          items:
            $ref: '#/components/schemas/GetPublicApiTaskResponseDto__schema0'
        - type: object
          propertyNames:
            type: string
          additionalProperties:
            $ref: '#/components/schemas/GetPublicApiTaskResponseDto__schema0'
  securitySchemes:
    personalApiKey:
      scheme: bearer
      bearerFormat: adam_pk_…
      description: >-
        A personal API key created in Adam. Send it as `Authorization: Bearer
        <key>`.
      type: http

````
