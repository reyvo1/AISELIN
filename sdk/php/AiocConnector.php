<?php
declare(strict_types=1);

final class AiocConnectorRuntime {
    private array $handlers = [];
    private array $verifiers = [];
    private array $rollbacks = [];
    public function __construct(private readonly string $applicationId) {}

    public function capability(string $name, callable $handler, ?callable $verifier = null, ?callable $rollback = null): void {
        if ($name === '' || isset($this->handlers[$name])) throw new InvalidArgumentException('capability name must be unique');
        $this->handlers[$name] = $handler;
        if ($verifier !== null) $this->verifiers[$name] = $verifier;
        if ($rollback !== null) $this->rollbacks[$name] = $rollback;
    }

    public function capabilities(): array {
        $names = array_keys($this->handlers); sort($names);
        return ['ok' => true, 'application_id' => $this->applicationId, 'capabilities' => $names];
    }

    public function execute(string $capability, array $parameters, bool $dryRun = false): array {
        if (!isset($this->handlers[$capability])) throw new RuntimeException("unsupported capability: {$capability}");
        $value = ($this->handlers[$capability])($parameters, $dryRun);
        if (!is_array($value)) throw new RuntimeException('connector handlers must return arrays');
        return $value;
    }

    public function verify(string $capability, array $payload): array {
        if (!isset($this->verifiers[$capability])) return ['ok' => true, 'mode' => 'default'];
        return ($this->verifiers[$capability])($payload, false);
    }

    public function rollback(string $capability, array $payload): array {
        if (!isset($this->rollbacks[$capability])) return ['ok' => false, 'supported' => false];
        return ($this->rollbacks[$capability])($payload, false);
    }

    public static function signEvent(string $secret, array $event, ?int $timestamp = null): array {
        $ts = (string)($timestamp ?? time());
        $body = json_encode($event, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $sig = hash_hmac('sha256', $ts . '.' . $body, $secret);
        return ['body' => $body, 'headers' => ['Content-Type' => 'application/json', 'X-AIOC-Timestamp' => $ts, 'X-AIOC-Signature' => 'sha256=' . $sig]];
    }
}
