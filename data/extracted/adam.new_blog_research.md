<!-- source: https://adam.new/blog/research fetched 2026-09-07T18:00:07+00:00 -->

PricingFAQDocs

Talk to usTry Adam

Blog

Research

# Things we're exploring

Dylan Anderson · May 24, 2026

Hi Dylan here! We have the privilege of working on some really cool technology at Adam. For the past few months we’ve been working on an agent that can perform actions in popular CAD programs. This is a frontier problem and we’re very excited by some of the progress we’ve made. Here are a few things we’re actively working on:

## Spatial Reasoning

Today’s LLMs on their own have a limited understanding of 3D space. We believe that this is a symptom of their predominantly text-based pre-training rather than a foundational limitation of their architecture. We're betting that this can be addressed by efficient post-training, by feeding them relevant context and training them in relevant environments to make leaps in spatial reasoning.

## Context Engineering

We’ve found that LLMs can reason through complex geometry when given the right context. Our job is to give the model the right scaffolding so it can act inside real CAD software. We tried topological query strings that describe for instance how an edge came to be. They survive small edits but balloon in size and are hard for a model to write. Instead we keep a tight, explicit record of vertices, edges, faces, and bodies in JSON, with fields chosen so every entity is uniquely identifiable. That shifts the problem to precision, so we pair the model with tools for search and selection. The loop is simple: fetch context, pick entities, apply an operation, validate, repeat. With the right context windows and a clean geometry index, the agent stays grounded and can drive fillets, chamfers and revolves without guessing.

## Post-training

We care about teaching a model to perform long horizon CAD tasks autonomously and reliably. This way it can plan, build CAD features and recover from mistakes just like a human engineer would. We ground it on real CAD sessions and carefully generated examples that teach the model how to use tools correctly in sequence, then lengthen tasks as it improves. We score what matters in production: parts compile, constraints solve, references stay stable, edits survive small sketch changes. Essentially we distill the knowledge of bigger models augmented with context engineering into smaller faster models.

This is an entirely new research field that will have a massive impact on the life of millions of engineers. Best practices for building CAD agents are unestablished and we are relishing the opportunity to experiment! If you’re the type of person who gets excited about working on big unsolved problems, please reach out!

### Products

CopilotCADAM

### Website

PricingBlogChangelogCareers

### Resources

DevelopersDocsGithubDiscord

### Socials

XInstagramYoutube

### Company

Terms of ServicePrivacy PolicyTrust & SecurityBug Bounty

COPYRIGHT ADAM AI LABS, INC 2026
