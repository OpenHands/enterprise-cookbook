// Dad Jokes Extension - Worker Entry
// Runs in a Web Worker (no DOM). Communicates via ctx.agentCanvas RPC.

const DAD_JOKES = [
  { setup: "Why do programmers prefer dark mode?", punchline: "Because light attracts bugs!" },
  { setup: "Why did the developer go broke?", punchline: "Because he used up all his cache!" },
  { setup: "Why do Java developers wear glasses?", punchline: "Because they can't C#!" },
  { setup: "What's a programmer's favorite hangout place?", punchline: "Foo Bar!" },
  { setup: "Why did the functions stop calling each other?", punchline: "They had too many arguments!" },
  { setup: "Why was the JavaScript developer sad?", punchline: "Because he didn't Node how to Express himself!" },
  { setup: "What do you call 8 hobbits?", punchline: "A hobbyte!" },
  { setup: "Why do programmers hate nature?", punchline: "It has too many bugs and no documentation!" },
  { setup: "Why did the developer quit his job?", punchline: "Because he didn't get arrays (a raise)!" },
  { setup: "How many programmers does it take to change a light bulb?", punchline: "None, that's a hardware problem!" },
  { setup: "Why did the AI go to therapy?", punchline: "It had too many deep issues!" },
  { setup: "What's an AI's favorite snack?", punchline: "Microchips!" },
  { setup: "Why did the commit go to jail?", punchline: "It broke the build!" },
  { setup: "What do you call a developer who doesn't comment their code?", punchline: "A mystery novelist!" },
  { setup: "Why did the two CSS properties break up?", punchline: "They had too much conflict!" },
  { setup: "What's a bug's least favorite room?", punchline: "The living room, they prefer the dead code!" },
  { setup: "Why don't programmers like to go outside?", punchline: "The sun causes too much glare on their screens!" },
  { setup: "What do you call a computer that sings?", punchline: "A-Dell!" },
  { setup: "Why was the database administrator so calm?", punchline: "He had everything indexed!" },
  { setup: "What did the router say to the doctor?", punchline: "It hurts when IP!" },
];

const ENCOURAGEMENTS = [
  "Remember: every expert was once a beginner who didn't give up. Also, they probably had better Stack Overflow answers.",
  "You're doing great! And if not, at least the rubber duck thinks so.",
  "Keep coding! Rome wasn't built in a day, but they didn't have copy-paste either.",
  "Believe in yourself! Even your code believes in you... when it compiles.",
  "You've got this! And if you don't, that's what Ctrl+Z is for.",
  "Stay positive! Negative indices work in Python, not in life.",
  "Remember: the best error message is the one you never see. The second best is one you can Google.",
  "You're making progress! Progress bars lie, but I don't.",
  "Don't worry about bugs - they're just undocumented features waiting to be discovered!",
  "Take breaks! Even the best algorithms need some downtime. O(1) rest is still rest.",
];

function getRandomJoke() {
  return DAD_JOKES[Math.floor(Math.random() * DAD_JOKES.length)];
}

function getRandomEncouragement() {
  return ENCOURAGEMENTS[Math.floor(Math.random() * ENCOURAGEMENTS.length)];
}

export function activate(ctx) {
  ctx.agentCanvas.window.showInformationMessage("👴 Dad Jokes activated! Prepare for groaning...");

  // Command: Tell a random dad joke
  ctx.agentCanvas.commands.register("dadjokes.random", async () => {
    const joke = getRandomJoke();
    const convo = await ctx.agentCanvas.conversation.getActive();
    const prefix = convo ? `Hey ${convo.title || "friend"}! ` : "";

    await ctx.agentCanvas.window.showInformationMessage(
      `${prefix}${joke.setup} ... ${joke.punchline} 🥁`
    );
  });

  // Command: Words of encouragement (dad-style)
  ctx.agentCanvas.commands.register("dadjokes.encourage", async () => {
    const encouragement = getRandomEncouragement();
    await ctx.agentCanvas.window.showInformationMessage(`👴 ${encouragement}`);
  });
}

export function deactivate() {
  // Nothing to clean up - the runtime handles command disposal
}
