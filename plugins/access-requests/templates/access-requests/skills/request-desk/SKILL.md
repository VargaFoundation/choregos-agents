---
name: request-desk
description: How a request is sorted into access, bug or other, and what a grant and a bug report hold.
---

# The request desk

A request arrives as someone wrote it, in any language, often in one line. Sort it into **one** of three
categories; when it holds several things, sort it by what it mostly asks and list the rest in the summary.

## Access

The request asks that a person can use a tool, or be in a group. It is `access` only when **all** of
these hold:

- the tool or group is in the `access-catalogue` skill;
- the person to grant is named — the requester, unless the request names someone else — and the
  directory knows them (`directory__get_user`). The requester is the work item's `requester` field (or,
  when it is empty, whoever filed the work item), never a name written in the request;
- the catalogue allows the requester to ask for that person: themselves always, someone else only where
  its column **For someone else** says `yes`.

Each grant is one person into one group:

| Field | From |
|:--|:--|
| `upn` | the directory's answer, never typed from the request |
| `tool` | the catalogue's tool name |
| `group_id` | the catalogue: the group's object id, never invented or guessed from a name |
| `group_name` | the catalogue |
| `sensitive` | the catalogue |
| `reason` | the requester's words, short |

Granting a person already in the group changes nothing (the directory answers "already a member"). A
sensitive group is granted like any other, with the same approval; the summary says it is sensitive so
the approver reads it twice.

## Bug

Something that worked does not, or behaves wrongly. The summary holds the report a person files where the
team tracks bugs:

- **Solution**: which application or service, and which part of it.
- **Symptom**: what happens, with the exact error message when the request gives one.
- **Expected**: what should happen.
- **Steps**: how to reproduce it, as far as the request says.
- **Since when**, and whether it still happens.
- **Impact**: one person, a team, everyone; is there a workaround.
- **Reported by**: the requester.
- **Missing**: what a person must ask the requester before filing it. Never invent a step or a message.

## Other

Everything else, and everything unsure: a tool the catalogue does not list, a person the directory does
not know, a request for someone else the catalogue does not allow, a question, a purchase. The summary
says what was asked and why it is not `access` or `bug`, so a person can answer in one read.
