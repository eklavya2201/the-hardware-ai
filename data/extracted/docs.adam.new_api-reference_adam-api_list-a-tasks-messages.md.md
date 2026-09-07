<!-- source: https://docs.adam.new/api-reference/adam-api/list-a-tasks-messages.md fetched 2026-09-07T18:02:05+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# List a task's messages

> Returns messages in chronological order. Use nextCursor to continue from the last message in the page.



## OpenAPI

````yaml /openapi/v1.json get /v1/tasks/{taskId}/messages
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
  /v1/tasks/{taskId}/messages:
    get:
      tags:
        - Adam API
      summary: List a task's messages
      description: >-
        Returns messages in chronological order. Use nextCursor to continue from
        the last message in the page.
      operationId: listTaskMessages
      parameters:
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
            default: 50
            maximum: 100
            minimum: 1
            type: integer
        - name: taskId
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
                $ref: '#/components/schemas/ListPublicApiTaskMessagesResponseDto'
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
    ListPublicApiTaskMessagesResponseDto:
      type: object
      properties:
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
                            #/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0
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
                            #/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0
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
                            #/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0
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
                            #/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0
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
        nextCursor:
          anyOf:
            - type: string
            - type: 'null'
      required:
        - messages
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
    ListPublicApiTaskMessagesResponseDto__schema0:
      anyOf:
        - type: string
        - type: number
        - type: boolean
        - type: 'null'
        - type: array
          items:
            $ref: '#/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0'
        - type: object
          propertyNames:
            type: string
          additionalProperties:
            $ref: '#/components/schemas/ListPublicApiTaskMessagesResponseDto__schema0'
  securitySchemes:
    personalApiKey:
      scheme: bearer
      bearerFormat: adam_pk_…
      description: >-
        A personal API key created in Adam. Send it as `Authorization: Bearer
        <key>`.
      type: http

````
