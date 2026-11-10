# Video 2 - First setup

The second video sets up the stack installed in video 1, in the web UI: the
first login, the agent, the base image, the scenario and the trainee. The part
before the recording runs on the host, from the root of the checkout; the
recorded part runs in the browser on the host.

---

## Before recording

```shell
# The VM is left as video 1 ended: the stack runs, the agent waits for approval.
# Log in to it and leave this shell open: it carries ports 8000 and 8080.
make -C dev/vm ssh

# In a second host shell: download the scenario bundle from the release.
curl -fsSL --cacert ~/.cache/jatszoter/vm/ca.crt --resolve github.com:443:127.0.0.1 \
    -o ~/Downloads/nginx-502.tar.gz \
    https://github.com/oberon-systems/jatszoter/releases/download/1.0.0/nginx-502.tar.gz
```

Keep the image URL and its sha256 at hand to paste:

```text
https://dl.rockylinux.org/pub/rocky/9.8/images/x86_64/Rocky-9-GenericCloud-Base-9.8-20260525.0.x86_64.qcow2
92c206cc6f790c61583247eefe87890f8828420662c17cacf247cec78ab4eec8
```

Open `http://localhost:8000/login` in a private browser window, so no cookie
of an earlier take is left.

---

## Recording

**Start recording here.**

- `# Log in with the account a new stack starts with.`

   Name `admin`, Password `admin`, click **Log in**.

- `# The first login asks for a password of your own.`

   Type it in **New password** and **Repeat new password**, click
   **Set password and log in**.

- `# The agent registered itself and waits for an operator to let it in.`

   Open **Agents**. Under **Pending registrations**, click **Approve** on
   `jatszoter-demo`.

- `# The agent is enabled; its next heartbeat shows capacity and accelerator.`

   Wait for the row under **Agents** to show the state `enabled` and the
   accelerator `kvm`.

- `# Register the base disk the scenario boots from.`

   Open **Scenarios and images**, the **Images** tab, click
   **Register image**.

- `# The name is the one scenario.yaml gives; the digest pins the file.`

   Name `rocky-9`, URL and Digest pasted from above, click **Register**.

- `# Register the scenario from its bundle, the tar.gz of the release.`

   Open the **Scenarios** tab, click **Register scenario**.

- `# Upload the bundle; the API checks it and stores this version.`

   **Bundle file**: choose `~/Downloads/nginx-502.tar.gz`, click
   **Register**. The row `nginx-502`, version `3`, image `rocky-9` appears.

- `# Add the trainee: the person who will get the test assignment.`

   Open **Users**, click **New user**.

- `# A trainee works through a link, so no password is needed.`

   Name `beta`, Role `trainee`, Comment `test assignment`, click
   **Create user**.

**Stop recording here.**
