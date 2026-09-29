Complaint Radar in plain English

The one-sentence version: it's a smoke detector for banks. It reads every public complaint customers file about banks, notices when complaints about something suddenly jump, and explains in plain words why it's happening.

The problem it solves

When someone has a bad experience with a bank, such as a surprise fee, a loan payment that went missing, or a debt collector who won't stop calling, they can file a complaint with a government agency called the CFPB. The CFPB posts these complaints online for anyone to read. There are millions of them, and thousands more arrive every week.

Banks have teams whose job is to watch these complaints and catch problems early. It's like a restaurant manager reading every Yelp review to see whether something went wrong in the kitchen. The trouble is there are far too many to read, and by the time a person notices a pattern, hundreds more customers may already be affected.

Complaint Radar does that reading and pattern-spotting automatically.

How it works: a mail room

Imagine a big company mail room.

1. The mailbag arrives (collecting the data).
Every day, a fresh bag of complaints comes in from the CFPB website. The system collects it and keeps an untouched copy of everything, the way you'd keep original receipts before doing your taxes. This is the "Bronze" layer, the raw pile.

2. Sorting and cleaning (the "Silver" layer).
The mail is messy. Some letters are duplicates, some have the date missing, and the same bank is written three different ways ("Wells Fargo," "WELLS FARGO & CO," "Wells Fargo Bank NA"). The government also renamed some complaint categories over the years, so "Bank account" and "Checking or savings account" really mean the same thing. This step tidies all of that up. It removes duplicates, fixes formats, and puts everything under consistent labels. It also keeps a record of what it threw out and why, so nothing disappears silently.

3. The summary report (the "Gold" layer).
Now the clean mail gets tallied: "This week, Bank X received 40 complaints about fees in Florida." Tally that for every bank, product, state and week, and you have a clear scoreboard. It also compares each bank with similar banks, because a giant bank will naturally get more complaints than a small one. Comparing like with like is fairer.

4. The smoke detector (spike alerts).
Each week, the system asks: "Is this number unusually high compared with the last few months?" If a bank normally gets 10 complaints a week about closed-account fees and suddenly gets 35, an alarm goes off.

There's one clever detail. Complaints only get posted once the bank responds, which can take up to 15 days. That means the most recent week always looks quieter than it really is, like counting votes before all the mail-in ballots arrive. The system adjusts for this so it doesn't raise false alarms, or miss real ones, just because the data is still coming in.

5. The AI analyst (the "why" behind the alarm).
Numbers tell you that something jumped, not why. The real story is in the customers' own written words. So AI reads those written complaints and tags each one: What's the actual root cause? How serious is it? Was a fee mentioned, and how much? Does it involve a vulnerable person, such as an elderly customer or someone in the military?

6. Ask it questions (the assistant).
A user can type a question like "Why did debt collection complaints spike in Florida last week?" The assistant looks up the numbers, pulls the most relevant real complaints, and answers something like: "Most of the increase comes from three issues… here are five example complaints (IDs #123, #456…)." It always shows its sources, so nobody has to take the AI's word for it. If there isn't enough data, it says so instead of guessing.

7. The screen people actually use (the app).
All of this sits behind one simple web page. On the left is a list of today's alarms, most serious first. Click one and you see the breakdown, the comparison with similar banks, and real example complaints. A chat box lets you ask follow-up questions.

How we know it's trustworthy

This is the part most hobby projects skip, and interviewers notice.

Checking the counts. The system proves no data was lost: this many complaints went in, this many came out, and this many were rejected, with the reasons.
Testing on the past. We check whether the smoke detector would have caught real past problems, before trusting it on new ones.
Grading the AI. A person labels about 200 complaints by hand, and we measure how often the AI agrees. The assistant also takes a regular "exam" of about 60 questions, and a new version isn't used unless it passes.
Where Databricks fits

Databricks is the building everything lives in. It stores the data, runs the daily cleaning steps on a schedule, hosts the AI, and serves the app, all in one place with access controls. We're using its free version, so the project costs nothing to run. That's why it only analyzes recent complaints with AI instead of all several million: the free version limits how much you can use each day.