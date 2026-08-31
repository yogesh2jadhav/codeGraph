package com.demo.web;

import com.demo.svc.OrderService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    @GetMapping("/{id}")
    public Order get(@PathVariable String id) {
        return service.findOrder(id);
    }

    @PostMapping
    public Order place(@RequestBody Order order) {
        return service.place(order);
    }
}
