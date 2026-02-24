# Bot Command Usage

This document describes the slash commands and message-based prompt syntax supported by the Discord bot.

## Slash Commands

All commands below are invoked as Discord application (slash) commands. Only users listed in the `ALLOWED_USER_IDS` environment variable can use these commands.

### `/hello`
**Description:** Responds with a greeting message chosen via autocomplete.

| Option   | Type   | Description              | Choices                                         |
|----------|--------|--------------------------|-------------------------------------------------|
| greeting | string | Greet me!                | Hello, world!  <br> Hi there!  <br> Greetings!  <br> Howdy! |

### `/set_instructions`
**Description:** Sets the instructions on which **all** Agents will operate.

| Option           | Type   | Description          |
|------------------|--------|----------------------|
| new_instructions | string | The new instructions |

### `/spawn`
**Description:** Registers a unique spawn ID that can be used for future prompts.

| Option                   | Type    | Description                                                            | Default           |
|--------------------------|---------|------------------------------------------------------------------------|-------------------|
| spawn_id                 | string  | The ID under which you will reference the Codex Instance (alphanumeric, max 64 chars) | _required_        |
| working_dir              | string  | The working directory for the agent to operate in                      | _required_        |
| provider                 | string  | The provider to use (must be one of `ALLOWED_PROVIDERS`)                | openai            |
| model                    | string  | The model to use                                                       | codex-mini-latest |
| execution_mode           | string  | Where to run Codex for this agent (`docker` or `host`)                 | `DEFAULT_EXECUTION_MODE` |
| verbosity                | string  | `answers` (responses + token usage) or `verbose` (also thoughts/tools) | `DEFAULT_AGENT_VERBOSITY` |
| leak_env                 | boolean | Leak host environment variables into the Codex runtime if allowed      | false             |
| allow_create_working_dir | boolean | If set to true, it will create the working dir if it does not exist      | true              |

### `/set_provider`
**Description:** Change the provider for an existing Agent.

| Option   | Type   | Description         | Default |
|----------|--------|---------------------|---------|
| spawn_id | string | The ID of the Agent | _required_ |
| provider | string | The Provider to use | openai  |

### `/set_model`
**Description:** Change the model for an existing Agent.

| Option   | Type   | Description        | Default           |
|----------|--------|--------------------|-------------------|
| spawn_id | string | The ID of the Agent | _required_        |
| model    | string | The model to use    | codex-mini-latest |

### `/kill`
**Description:** Kill active processes for a spawn ID.

| Option            | Type    | Description                                                      | Default |
|-------------------|---------|------------------------------------------------------------------|---------|
| spawn_id          | string  | The spawn ID to kill processes for                              | _required_ |
| delete            | boolean | Delete the agent fully (including Docker container)             | false   |
| revert_chat_state | boolean | Revert chat state to before your last message if process was killed | true    |

### `/delete_all_agents`
**Description:** Kill all agents and delete the `spawns.json` file.

| Option       | Type   | Description                          |
|--------------|--------|--------------------------------------|
| confirmation | string | Type `CONFIRM` to confirm the action |

### `/list`
**Description:** List active workers and their processes with PID and runtime.

_No options._

## Message-based Interaction: `on_message`

The bot also listens for direct messages in this form to forward prompts to existing agents:

```
@Bot to <spawn_id>: <prompt>
```

Here, `@Bot` is a mention of the bot (e.g. `<@123456789012345678>`), `<spawn_id>` is the agent's ID, and `<prompt>` is the message to pass.

The reason for this design decision is that it allows the user to write a normal message, potentially with code blocks or file attachments,
without the restrictions of slash commands and the inconvenience of writing long messages in slash command text fields getting in their way.
Additionally, it makes it much easier to quickly scan chats, because you can see what you wrote without clicking on the slash command message.

**Example:**
```
<@123456789012345678> to dev: Please refactor the authentication module.
```

The handler ignores messages from other bots or from users not on the allow-list, matches the syntax, validates the `spawn_id`, and then forwards the prompt to the corresponding agent (or reports an error if the spawn ID is unknown).
