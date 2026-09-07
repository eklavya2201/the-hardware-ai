<!-- source: https://adam.new/blog/sliders fetched 2026-09-07T18:00:08+00:00 -->

PricingFAQDocs

Talk to usTry Adam

Blog

Product

# How we improved the slider experience in Adam

Aaron Li · May 24, 2026

## Background

The slider experience in the Adam web app has always been a pain point of the user experience. What initially started as a vibe-coded shadcn component slowly became an eyesore that caused a lot of user frustration. As the app grew, this component was pulled into more flows. Its flaws became increasingly apparent, eventually reaching a point where it needed a proper redesign.

## Pain points

When we looked closely at how users interacted with the slider, we uncovered several consistent issues:

1.

### Narrow Trigger Zone

Despite its bright appearance, the slider was surprisingly hard to grab. In usability sessions, many users missed the hitbox entirely on their first attempt, often having to try multiple times before it responded. This pattern made it clear the interactive area was simply too small.

2.

### Lack of Visual Feedback

The slider also gave no hover or active feedback. Without visual cues to confirm it was ready to be moved, users hesitated or second‑guessed whether their actions were registering.

3.

### Sense of Relativity and Scale

Another quirk came from the fact that every slider started in the dead center. Users weren’t sure how to judge scale. Was 6mm for a hole tiny or oversized? Centering everything blurred the sense of range, making values harder to interpret at a glance.

4.

### Spacing and Alignment

This one was more aesthetically related. The lack of consistent spacing and alignment made the panel feel cramped and unbalanced, as if it was always missing just a bit of breathing room.

## Process

Prototyping Micro-Interactions: Origami Studio

The first thing we wanted to tackle was how the slider would feel to use. While Figma was great for layout and sizing, it didn’t offer the level of interactivity we wanted from an early prototype.
Early static sketches in Figma
This is where Origami Studio came in. Its ability to chain interactions together makes it a great testing ground for fine‑tuning micro‑interactions. Specifically, we used it to adjust the hover and click transition animations of the slider to make its movement feel natural and responsive.

Adding more interactivity with Cursor

At this point, we felt comfortable bringing the design into code and began implementing it with Cursor. Writing software as a means of prototyping has its own advantages.

Some features we wanted to implement were more programmatic in nature, which made them easier to prototype through code than mocking up in Figma or Origami.

One of these was the reset value feature. This was an area where we took a bolder approach that didn’t follow existing conventions. Instead of adding a dedicated button, we used a subtle vertical line to indicate the original value. To make it feel just as organic as the slider itself, we applied the same opacity and scale animations here.

We also realized that showing sliders all starting in the middle wasn't very helpful. So we tied the default position to its value range. For example, a default of 6mm would receive a 0–10mm scale placing the slider at 60%, while 23 on a 0–100 scale sits at 23%. This gave the users a clearer sense of scale, helping them judge whether a number was low or high in context.

Final Touches: Adapting Interactions for Mobile vs Desktop

Perhaps the most interesting discovery we’ve made during this process was found when we were internally testing the interaction.

When we first designed the slider, its position didn’t follow the cursor directly. The idea was that this would let users make large adjustments without needing precise aim: dragging anywhere on the track would change the value. This approach was inspired by the way volume and brightness sliders work on iOS. What we didn’t anticipate was that many desktop users actually preferred to click directly on the slider to jump to a specific value, rather than drag.

This presented us with 2 choices:

Option A: A slider that will react to drag movement regardless of the cursor's position

Pro: Very easy to slide, does not require high precision from the user

Con: Can’t click to jump to a specific value

Option B: A slider that will jump to the location of the cursor when the user click on the slider

Pro: Follows conventional UX patterns

Cons: Requires more precise movements from the user

Ultimately, the answer came down to designing for the correct medium. We ended up using Option A for mobile and Option B for desktop. Reason being that cursors on desktops can often deliver a very high degree of precision, whereas our original design intent in Option A benefitted better for mobile experiences where our fingers can’t be as precise. Implementing this was surprisingly easy, as CSS media queries can detect the type of device the user is using.

## Takeaways

Looking back, what started as a rough idea of a better slider ended with one that feels intentional, flexible, and adaptable to different devices. Along the way we discovered that small details like the size of the trigger zone, hover states, and how defaults are displayed can have an outsized impact on usability. Ultimately, we came away with a stronger sense that good interaction design isn’t just about polish, but about questioning conventions, testing ideas, and adapting patterns to the medium. This helped us shape a mindset that will guide how we approach building interfaces in the future.

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
