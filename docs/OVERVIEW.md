# Loging Inbox Overview

## Goal

Log all incoming messages from Whatsapp Engine.

Supported data:
- text messages
- image messages
- metadata for each message
- locally stored image files
- image context extracted by an agent

## Source

Source of events:
- Whatsapp Engine outgoing webhook / event delivery

## Updated storage design

- SQLite database for structured metadata and extracted context
- local filesystem for original media/image files

## Updated image flow

For incoming image messages:
1. store original image file locally
2. call an agent to inspect the image
3. extract useful context / description from the image
4. save that context to SQLite together with message metadata

This makes the inbox searchable not only by message text but also by image meaning.

## Viewer

Project should provide a log viewer so messages can be reviewed later.

Planned viewer features:
- list inbox logs
- filter by sender / chat / type
- open message details
- preview image if available
- show extracted image context

## Basic architecture

```text
Whatsapp Engine
   -> webhook POST
   -> Loging Inbox receiver
   -> save image locally (if media)
   -> call agent for image context
   -> SQLite stores metadata + extracted image context
   -> viewer UI / API
```
