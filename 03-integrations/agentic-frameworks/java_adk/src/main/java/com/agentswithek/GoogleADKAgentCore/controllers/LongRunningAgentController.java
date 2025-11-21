package com.agentswithek.GoogleADKAgentCore.controllers;

import com.agentswithek.GoogleADKAgentCore.entities.LongRunningInvocationRequest;
import com.agentswithek.GoogleADKAgentCore.entities.LongRunningInvocationResponse;
import com.agentswithek.GoogleADKAgentCore.entities.PingResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.netty.http.client.HttpClient;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/**
 * REST Controller for long-running HTTP invocations.
 * Migrated from Python asyncagent/main.py
 *
 * This controller makes asynchronous HTTP calls to an external service
 * with configurable timeouts to handle long-running operations.
 */
@RestController
public class LongRunningAgentController {

    private static final Logger logger = LoggerFactory.getLogger(LongRunningAgentController.class);
    private static final String TARGET_URL = "http://10.0.1.184:8080/invoke";

    private final WebClient webClient;
    private final ObjectMapper objectMapper;

    public LongRunningAgentController() {
        this.objectMapper = new ObjectMapper();
        // Configure HTTP client with specific timeout settings
        // Matching Python's timeout config:
        // - connect timeout: 10 seconds
        // - read timeout: disabled (null)
        // - total timeout: disabled (null)
        HttpClient httpClient = HttpClient.create()
                .responseTimeout(Duration.ZERO)  // No read timeout
                .option(io.netty.channel.ChannelOption.CONNECT_TIMEOUT_MILLIS, 10000); // 10s connect timeout

        this.webClient = WebClient.builder()
                .clientConnector(new ReactorClientHttpConnector(httpClient))
                .build();

        logger.info("LongRunningAgentController initialized with WebClient");
    }

    /**
     * POST /invocations - Handle invocation requests with long-running HTTP calls.
     *
     * Receives a request with duration parameter and makes an async HTTP call
     * to an external service that may take a long time to respond.
     *
     * Parse raw body directly since bedrock-agentcore doesn't set Content-Type header
     *
     * @param rawBody The raw request body bytes
     * @return Response with output from the external service
     */
    @PostMapping(value = "/invocations", consumes = "*/*")
    public ResponseEntity<?> productionAgent(@RequestBody byte[] rawBody) {
        String rawBodyString = new String(rawBody, StandardCharsets.UTF_8);
        logger.info("Raw body received: {}", rawBodyString.isEmpty() ? "empty" : rawBodyString);

        LongRunningInvocationRequest request;
        try {
            request = objectMapper.readValue(rawBody, LongRunningInvocationRequest.class);
            logger.info("Parsed JSON data: {}", request);
        } catch (Exception e) {
            logger.error("JSON decode error: {}", e.getMessage());
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "Invalid JSON in request body"));
        }

        if (request == null || request.getInput() == null) {
            logger.error("Invalid payload - missing 'input' key: {}", request);
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "Missing 'input' in request body"));
        }

        int duration = request.getDuration();
        logger.info("Starting production_agent with duration: {}", duration);

        try {
            String url = TARGET_URL + "?delay=" + duration;
            logger.info("Attempting connection to: {}", url);
            logger.info("Using WebClient with no read timeout");

            // Make async HTTP GET request and block for the result
            // This matches the Python async/await behavior
            Object output = webClient.get()
                    .uri(url)
                    .retrieve()
                    .bodyToMono(Object.class)
                    .doOnNext(response -> logger.info("Response received!"))
                    .block(); // Block waiting for response (no timeout)

            logger.info("Request completed successfully");
            return ResponseEntity.ok(LongRunningInvocationResponse.of(output));

        } catch (WebClientResponseException.BadGateway e) {
            logger.error("Bad Gateway error: {}", e.getMessage());
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY)
                    .body(Map.of("error", "Bad Gateway: " + e.getMessage()));

        } catch (WebClientResponseException e) {
            logger.error("HTTP error: {}", e.getMessage());
            return ResponseEntity.status(e.getStatusCode())
                    .body(Map.of("error", "HTTP error: " + e.getMessage()));

        } catch (io.netty.handler.timeout.ReadTimeoutException e) {
            logger.error("Timeout error: {}", e.getMessage());
            return ResponseEntity.status(HttpStatus.GATEWAY_TIMEOUT)
                    .body(Map.of("error", "Timeout error: " + e.getMessage()));

        } catch (Exception e) {
            logger.error("Unexpected error: {}: {}", e.getClass().getSimpleName(), e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(Map.of("error", "Internal error: " + e.getMessage()));
        }
    }

    /**
     * GET /ping - Health check endpoint.
     *
     * Verifies that the service is operational and ready to handle requests.
     *
     * @return Health status response
     */
    @GetMapping("/ping")
    public ResponseEntity<PingResponse> ping() {
        logger.info("HEALTHY");
        return ResponseEntity.ok(PingResponse.healthy());
    }
}
